import logging
import time
from decimal import Decimal

import requests
from countryinfo import CountryInfo
from django.contrib.auth import authenticate, get_user_model
from django.db.models import Sum, Count, Q
from django.db.models.functions import Coalesce
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import update_session_auth_hash
from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions, status
from common.cache_utils import get_or_set_cache, delete_cache_keys

from common.cache_keys import (
    admin_profile_key,
    admin_users_key,
    admin_dashboard_stats_key,
    user_profile_key,
    user_summary_key
)

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import permissions, status
from .serializers_admin import AdminLoginSerializer



from boost.models import BoostRequest
from payments.models import Deposit
from virtualnumbers.models import VirtualNumber

from .models import Wallet
from .serializers import RegisterSerializer, UserSerializer

User = get_user_model()
logger = logging.getLogger(__name__)



def get_currency_from_country(country_name: str) -> str:
   
    try:
        if not country_name:
            return "NGN"
        country = CountryInfo(country_name)
        currencies = country.currencies()
        if currencies and len(currencies) > 0:
            return currencies[0]
    except Exception:
        pass
    return "NGN"


def get_exchange_rates(base_currency: str) -> dict:
    cache_key = f"exchange_rates:{base_currency}"

    def fetch_rates():
        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{base_currency}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                return data.get("rates", {})
        except Exception as e:
            logger.warning(f"Failed to fetch exchange rates: {e}")
        return {}

    return get_or_set_cache(cache_key, fetch_rates, timeout=3600)


def convert_currency(amount, from_currency: str, to_currency: str):
    """
    Convert amount from one currency to another using cached rates.
    """
    if from_currency == to_currency:
        return amount

    rates = get_exchange_rates(from_currency)
    rate = rates.get(to_currency)
    if rate:
        return round(Decimal(str(amount)) * Decimal(str(rate)), 2)

    return amount


def wallet_payload(wallet, currency_fallback: str = "NGN") -> dict:
    """The single shape every endpoint uses to describe a user's wallet.

    `balance` / `reserved_balance` are the MAIN wallet (spent on the website).
    `api_balance` / `api_reserved_balance` are the SEPARATE developer-API pool.
    The frontend sums balance + api_balance for the sidebar "total" view, while
    the dashboard shows `balance` (main) alone.
    """
    if not wallet:
        return {
            "balance": 0.0,
            "reserved_balance": 0.0,
            "api_balance": 0.0,
            "api_reserved_balance": 0.0,
            "currency": currency_fallback,
        }
    return {
        "balance": float(wallet.balance or 0),
        "reserved_balance": float(getattr(wallet, "reserved_balance", 0) or 0),
        "api_balance": float(getattr(wallet, "api_balance", 0) or 0),
        "api_reserved_balance": float(getattr(wallet, "api_reserved_balance", 0) or 0),
        "currency": wallet.currency,
    }


def build_user_summary(user) -> dict:
    # One aggregate query per model (3 total) instead of six. Each combines its
    # SUM and COUNT using conditional aggregation, and is backed by composite
    # indexes on (user, status) / (user, charged) so it stays fast as data grows.
    zero = Decimal("0")

    dep = Deposit.objects.filter(user=user).aggregate(
        deposited=Coalesce(Sum("amount", filter=Q(status="paid")), zero),
        paid_count=Count("id", filter=Q(status="paid")),
    )
    num = VirtualNumber.objects.filter(user=user).aggregate(
        spent=Coalesce(Sum("cost", filter=Q(charged=True)), zero),
        # "Purchased" = numbers actually kept (exclude cancelled/refunded & failed).
        total_count=Count("id", filter=~Q(status__in=["Cancelled", "Failed"])),
    )
    bst = BoostRequest.objects.filter(user=user).aggregate(
        spent=Coalesce(Sum("amount", filter=Q(amount__gt=0) & ~Q(status="Failed")), zero),
        # Count only successful boosts (exclude failed ones).
        total_count=Count("id", filter=~Q(status="Failed")),
    )

    deposited_total = dep["deposited"]
    paid_deposits_count = dep["paid_count"]
    numbers_spent_total = num["spent"]
    numbers_count = num["total_count"]
    boost_spent_total = bst["spent"]
    boost_count = bst["total_count"]

    overall_spending = numbers_spent_total + boost_spent_total

    return {
        "totals": {
            "deposited": float(deposited_total),
            "spent_on_numbers": float(numbers_spent_total),
            "spent_on_boost": float(boost_spent_total),
            "overall_spending": float(overall_spending),
        },
        "counts": {
            "numbers_purchased": numbers_count,
            "deposits_paid": paid_deposits_count,
            "boost_requests": boost_count,
        },
    }


class RegisterManualView(generics.CreateAPIView):
  
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        if "fullName" in data:
            data["full_name"] = data.pop("fullName")

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        country_name = serializer.validated_data.get("country")
        currency = get_currency_from_country(country_name)

        Wallet.objects.get_or_create(user=user, defaults={"currency": currency})

        wallet = getattr(user, "wallet", None)
        if wallet and wallet.currency != currency:
            wallet.currency = currency
            wallet.save(update_fields=["currency"])

        refresh = RefreshToken.for_user(user)
        wallet = getattr(user, "wallet", None)

        response_data = {
            "user": {
                **UserSerializer(user).data,
                "wallet": wallet_payload(wallet, currency),
            },
            "summary": build_user_summary(user),
            "token": str(refresh.access_token),
        }

        return Response(response_data, status=status.HTTP_201_CREATED)


class LoginView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "login"  # rate-limit brute-force attempts (10/min per IP)

    def post(self, request):
        try:
            # Accept email OR username in the same field.
            identifier = (request.data.get("email") or request.data.get("login")
                          or request.data.get("username") or "").strip()
            password = request.data.get("password")

            if not identifier or not password:
                return Response(
                    {"error": "Email/username and password are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if "@" in identifier:
                account = User.objects.filter(email__iexact=identifier).first()
            else:
                account = User.objects.filter(username__iexact=identifier).first()

            user = authenticate(request, email=account.email, password=password) if account else None
            if not user:
                return Response(
                    {"error": "Invalid credentials."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            wallet = getattr(user, "wallet", None)
            if not wallet:
                currency = get_currency_from_country(user.country)
                wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})

            refresh = RefreshToken.for_user(user)

            return Response(
                {
                    "user": {
                        **UserSerializer(user).data,
                        "wallet": wallet_payload(wallet),
                    },
                    "summary": build_user_summary(user),
                    "token": str(refresh.access_token),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Login error: {str(e)}", exc_info=True)
            return Response(
                {"error": "An internal error occurred during login. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MeView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        def fetch_me():
            wallet = getattr(user, "wallet", None)
            if not wallet:
                currency = get_currency_from_country(user.country)
                wallet_obj, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})
            else:
                wallet_obj = wallet

            return {
                "user": {
                    **UserSerializer(user).data,
                    "wallet": wallet_payload(wallet_obj),
                },
                "summary": build_user_summary(user),
            }

        cache_key = user_profile_key(user.id)
        data = get_or_set_cache(cache_key, fetch_me, timeout=300)
        return Response(data, status=status.HTTP_200_OK)

class UpdateUserProfileView(generics.UpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user

    def put(self, request, *args, **kwargs):
        user = self.get_object()
        data = request.data.copy()

        old_password = data.get("oldPassword")
        new_password = data.get("newPassword")

        serializer = self.get_serializer(user, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        
        if new_password:
            if not old_password:
                return Response(
                    {"error": "Old password is required to change password."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not user.check_password(old_password):
                return Response(
                    {"error": "Old password is incorrect."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.set_password(new_password)
            user.save()

        wallet = getattr(user, "wallet", None)
        if not wallet:
            currency = get_currency_from_country(user.country)
            wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})

        if "country" in serializer.validated_data:
            new_currency = get_currency_from_country(user.country)
            if wallet.currency != new_currency:
                wallet.currency = new_currency
                wallet.save(update_fields=["currency"])

        delete_cache_keys(
            user_profile_key(user.id),
            user_summary_key(user.id),
        )        

        return Response(
            {
                "message": "Profile updated successfully.",
                "user": {
                    **UserSerializer(user).data,
                    "wallet": wallet_payload(wallet),
                },
                "summary": build_user_summary(user),
            },
            status=status.HTTP_200_OK,
        )



@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def admin_login(request):
    serializer = AdminLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.validated_data, status=status.HTTP_200_OK)

@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_dashboard_stats(request):
    def fetch_stats():
        total_users = User.objects.count()
        total_deposits = Deposit.objects.count()
        pending_deposits = Deposit.objects.filter(status="pending").count()
        paid_deposits = Deposit.objects.filter(status="paid").count()
        failed_deposits = Deposit.objects.filter(status="failed").count()

        return {
            "total_users": total_users,
            "total_deposits": total_deposits,
            "pending_deposits": pending_deposits,
            "paid_deposits": paid_deposits,
            "failed_deposits": failed_deposits,
        }

    data = get_or_set_cache(admin_dashboard_stats_key(), fetch_stats, timeout=120)
    return Response(data, status=200)


@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_profile(request):
    user = request.user

    def fetch_profile():
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": getattr(user, "full_name", ""),
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "date_joined": user.date_joined.isoformat() if user.date_joined else None,
        }

    data = get_or_set_cache(admin_profile_key(user.id), fetch_profile, timeout=300)
    return Response(data, status=200)


@api_view(["PUT"])
@permission_classes([permissions.IsAdminUser])
def admin_update_profile(request):
    user = request.user

    username = request.data.get("username", "").strip()
    full_name = request.data.get("full_name", "").strip()

    if username:
        existing_user = User.objects.filter(username=username).exclude(id=user.id).first()
        if existing_user:
            return Response({"error": "Username already exists"}, status=400)
        user.username = username

    if hasattr(user, "full_name"):
        user.full_name = full_name

    user.save()

    delete_cache_keys(admin_profile_key(user.id), admin_users_key())

    return Response({
        "message": "Profile updated successfully"
    }, status=200)


@api_view(["PUT"])
@permission_classes([permissions.IsAdminUser])
def admin_change_password(request):
    user = request.user

    current_password = request.data.get("current_password", "")
    new_password = request.data.get("new_password", "")
    confirm_password = request.data.get("confirm_password", "")

    if not current_password or not new_password or not confirm_password:
        return Response(
            {"error": "All password fields are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not user.check_password(current_password):
        return Response(
            {"error": "Current password is incorrect"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_password != confirm_password:
        return Response(
            {"error": "New password and confirm password do not match"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 6:
        return Response(
            {"error": "New password must be at least 6 characters"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_password)
    user.save()

    update_session_auth_hash(request, user)

    delete_cache_keys(admin_profile_key(user.id))

    return Response(
        {"message": "Password updated successfully"},
        status=status.HTTP_200_OK,
    )
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def cache_test(request):
    try:
        cache.set("test_key", "redis is working", timeout=60)
        value = cache.get("test_key")

        return Response({
            "success": True,
            "message": "Cache test worked",
            "cached_value": value
        })
    except Exception as e:
        return Response({
            "success": False,
            "error": str(e)
        }, status=500)