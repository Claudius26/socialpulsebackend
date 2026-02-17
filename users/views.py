import logging
import time
from decimal import Decimal

import requests
from countryinfo import CountryInfo
from django.contrib.auth import authenticate, get_user_model
from django.db.models import Sum
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from boost.models import BoostRequest
from payments.models import Deposit
from virtualnumbers.models import VirtualNumber

from .models import Wallet
from .serializers import RegisterSerializer, UserSerializer

User = get_user_model()
logger = logging.getLogger(__name__)


exchange_cache = {}


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
    
    now = time.time()
    if base_currency in exchange_cache:
        cached = exchange_cache[base_currency]
        if now - cached["timestamp"] < 3600:
            return cached["rates"]

    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{base_currency}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            rates = data.get("rates", {})
            exchange_cache[base_currency] = {"rates": rates, "timestamp": now}
            return rates
    except Exception as e:
        logger.warning(f"Failed to fetch exchange rates: {e}")

    return {}


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


def build_user_summary(user) -> dict:
    
    deposited_total = (
        Deposit.objects.filter(user=user, status="paid")
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0")
    )

    
    numbers_spent_total = (
        VirtualNumber.objects.filter(user=user, charged=True)
        .aggregate(total=Sum("cost"))
        .get("total")
        or Decimal("0")
    )

    boost_spent_total = (
        BoostRequest.objects.filter(user=user)
        .exclude(status="Failed")
        .filter(amount__gt=0)
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0")
    )

    numbers_count = VirtualNumber.objects.filter(user=user).count()
    paid_deposits_count = Deposit.objects.filter(user=user, status="paid").count()
    boost_count = BoostRequest.objects.filter(user=user).count()

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
                "wallet": {
                    "balance": wallet.balance if wallet else 0,
                    "reserved_balance": getattr(wallet, "reserved_balance", 0) if wallet else 0,
                    "currency": wallet.currency if wallet else currency,
                },
            },
            "summary": build_user_summary(user),
            "token": str(refresh.access_token),
        }

        return Response(response_data, status=status.HTTP_201_CREATED)


class LoginView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            email = request.data.get("email")
            password = request.data.get("password")

            if not email or not password:
                return Response(
                    {"error": "Email and password are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = authenticate(request, email=email, password=password)
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
                        "wallet": {
                            "balance": wallet.balance,
                            "reserved_balance": getattr(wallet, "reserved_balance", 0),
                            "currency": wallet.currency,
                        },
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

        wallet = getattr(user, "wallet", None)
        if not wallet:
            currency = get_currency_from_country(user.country)
            wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})

        return Response(
            {
                "user": {
                    **UserSerializer(user).data,
                    "wallet": {
                        "balance": wallet.balance,
                        "reserved_balance": getattr(wallet, "reserved_balance", 0),
                        "currency": wallet.currency,
                    },
                },
                "summary": build_user_summary(user),
            },
            status=status.HTTP_200_OK,
        )

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

        return Response(
            {
                "message": "Profile updated successfully.",
                "user": {
                    **UserSerializer(user).data,
                    "wallet": {
                        "balance": wallet.balance,
                        "reserved_balance": getattr(wallet, "reserved_balance", 0),
                        "currency": wallet.currency,
                    },
                },
                "summary": build_user_summary(user),
            },
            status=status.HTTP_200_OK,
        )
