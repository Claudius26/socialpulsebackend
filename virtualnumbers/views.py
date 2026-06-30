import os
import requests
from decimal import Decimal
from dotenv import load_dotenv

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from rest_framework import generics, permissions
from rest_framework.response import Response

from .models import VirtualNumber, ReceivedSMS
from .serializers import VirtualNumberSerializer
from users.models import Wallet
from common.cache_utils import invalidate_user_wallet_caches
from common.providers import get_otp_provider, ProviderError
from common.fx import get_rate, convert, FxError
from common.currencies import quantize

load_dotenv()

ZAPOTP_API_KEY = os.getenv("ZAPOTP_API_KEY")
ZAPOTP_BASE_URL = "https://zapotp.com/account/api/v1"
ZAPOTP_CANCEL_URL = "https://www.zapotp.com/account/smspool/cancel_order.php"

ZAPOTP_HEADERS = {
    "Authorization": f"Bearer {ZAPOTP_API_KEY}",
    "Content-Type": "application/json",
}


def safe_decimal(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def normalize_country(country: str) -> str:
    return (country or "").strip().upper()


def normalize_service(service: str) -> str:
    return (service or "").strip().lower()


def minutes_since(dt):
    if not dt:
        return 0
    diff = timezone.now() - dt
    return int(diff.total_seconds() // 60)


def wallet_available(wallet):
    return safe_decimal(wallet.balance) - safe_decimal(getattr(wallet, "reserved_balance", "0"))


# Shown to users whenever the PROVIDER (ZapOTP) fails — including when OUR
# provider balance is low. Never expose the raw provider message: a customer
# must not learn anything about our funding/operations.
SERVICE_UNAVAILABLE = "This service is temporarily unavailable. Please try again later."


def user_currency(request):
    """The wallet currency of the requesting user (defaults to NGN)."""
    u = getattr(request, "user", None)
    if u and getattr(u, "is_authenticated", False):
        w = getattr(u, "wallet", None)
        if w and getattr(w, "currency", None):
            return w.currency
    return "NGN"


class GetServicesView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        service = normalize_service(request.query_params.get("service"))
        country = normalize_country(request.query_params.get("country"))

        if not service or not country:
            return Response({"error": "service and country are required"}, status=400)

        try:
            data = get_otp_provider().list_pools(country, service)
        except ProviderError:
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        if not isinstance(data, dict) or data.get("status") != "success" or "data" not in data:
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        profit = safe_decimal(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3))

        # Show prices in the user's wallet currency.
        cur = user_currency(request)
        rate = Decimal("1")
        if cur != "NGN":
            try:
                rate = get_rate("NGN", cur)
            except FxError:
                cur = "NGN"  # fall back to NGN display rather than break the list

        services = []
        for item in data.get("data", []):
            base_price = safe_decimal(item.get("price", 0))
            final_price = (base_price * (Decimal("1") + profit)).quantize(Decimal("0.01"))
            display_price = quantize(final_price * rate, cur)

            services.append(
                {
                    "pool_id": item.get("pool"),
                    "name": item.get("name"),
                    "success_rate": item.get("success_rate"),
                    "base_price": float(base_price),
                    "price_with_profit": float(final_price),  # NGN (internal)
                    "price": float(display_price),             # user's currency
                    "currency": cur,
                }
            )

        return Response({"country": country, "service": service, "services": services})


class PurchaseNumberView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        wallet = user.wallet

        service = normalize_service(request.data.get("service"))
        country = normalize_country(request.data.get("country"))
        pool_id = request.data.get("pool_id")
        provider = (request.data.get("provider") or "").strip().lower()

        if not service or not country or not pool_id:
            return Response({"error": "service, country and pool_id are required"}, status=400)

        try:
            services_data = get_otp_provider().list_pools(country, service)
        except ProviderError:
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        if not isinstance(services_data, dict) or services_data.get("status") != "success" \
                or "data" not in services_data:
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        selected_pool = None
        for item in services_data.get("data", []):
            if str(item.get("pool")) == str(pool_id):
                selected_pool = item
                break

        if not selected_pool:
            return Response({"error": "Selected pool not found"}, status=400)

        base_price = safe_decimal(selected_pool.get("price", 0))
        profit = safe_decimal(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3))
        final_price_ngn = (base_price * (Decimal("1") + profit)).quantize(Decimal("0.01"))

        # Charge in the wallet's own currency (convert the NGN price; round to the
        # currency's exact precision so nothing is lost). The stored cost is in
        # that currency, so charge/cancel later need no further conversion.
        cur = getattr(wallet, "currency", None) or "NGN"
        try:
            final_price = convert(final_price_ngn, "NGN", cur)
        except FxError:
            return Response({"error": "Currency conversion unavailable. Please try again."}, status=503)

        if wallet_available(wallet) < final_price:
            return Response({"error": "Insufficient wallet balance"}, status=400)

        import logging
        logger = logging.getLogger(__name__)
        try:
            order_data = get_otp_provider().rent(country, service, pool_id, provider)
        except ProviderError as e:
            logger.warning("Provider rent failed (%s/%s): %s", country, service, e)
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        if not isinstance(order_data, dict) or order_data.get("status") != "success":
            # e.g. our provider balance is low — log it, but show the user nothing
            # about our operations.
            logger.warning("Provider rent rejected (%s/%s): %s", country, service, order_data)
            return Response({"error": SERVICE_UNAVAILABLE}, status=503)

        order = order_data.get("data") or {}
        order_id = order.get("order_id")
        number = order.get("number")

        if not order_id or not number:
            return Response({"error": order_data}, status=400)

        with transaction.atomic():
            wallet.reserved_balance = safe_decimal(getattr(wallet, "reserved_balance", "0")) + final_price
            wallet.save()

            v = VirtualNumber.objects.create(
                user=user,
                country=country,
                service=service,
                phone_number=str(number),
                activation_id=str(order_id),
                cost=final_price,
                status="Pending",
                charged=False,
                sms_received_at=None,
                cancelled_at=None,
            )

        invalidate_user_wallet_caches(user.id)
        return Response(VirtualNumberSerializer(v).data, status=201)


class CancelNumberView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        activation_id = request.data.get("activation_id")
        if not activation_id:
            return Response({"error": "activation_id is required"}, status=400)

        vn = VirtualNumber.objects.filter(
            user=request.user,
            activation_id=str(activation_id),
        ).first()

        if not vn:
            return Response({"error": "Virtual number not found"}, status=404)

        # The ONLY thing that truly blocks a cancel is an SMS already received
        # (then the number was used/charged). Everything else is cancellable.
        if vn.sms_received_at or vn.charged:
            return Response(
                {"error": "You cannot cancel after an SMS has been received."}, status=400
            )

        # Idempotent: if it's already cancelled (e.g. the auto-cancel cron beat us,
        # or a double-tap), report success — the hold was already released.
        if vn.status == "Cancelled":
            return Response(
                {"success": True, "already_cancelled": True, "activation_id": vn.activation_id},
                status=200,
            )

        # ZapOTP has no cancel endpoint — cancellation is internal. Lock the row so
        # we never race the cron, release the held funds, and mark it Cancelled.
        with transaction.atomic():
            locked = VirtualNumber.objects.select_for_update().get(pk=vn.pk)
            if locked.sms_received_at or locked.charged:
                return Response(
                    {"error": "You cannot cancel after an SMS has been received."}, status=400
                )
            if locked.status != "Cancelled":
                wallet = Wallet.objects.select_for_update().get(user=request.user)
                wallet.reserved_balance = max(
                    Decimal("0"), safe_decimal(wallet.reserved_balance) - safe_decimal(locked.cost)
                )
                wallet.save(update_fields=["reserved_balance"])
                locked.status = "Cancelled"
                locked.cancelled_at = timezone.now()
                locked.save(update_fields=["status", "cancelled_at"])

        invalidate_user_wallet_caches(request.user.id)
        return Response(
            {
                "success": True,
                "released_amount": float(vn.cost),
                "activation_id": vn.activation_id,
            },
            status=200,
        )


class GetSMSView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, activation_id):
        vn = VirtualNumber.objects.filter(
            user=request.user,
            activation_id=str(activation_id),
        ).first()

        if not vn:
            return Response({"error": "Virtual number not found"}, status=404)

        if vn.status in ("Cancelled", "Expired", "Failed"):
            return Response({"error": "This number is not active"}, status=400)

        try:
            data = get_otp_provider().get_sms(activation_id)
        except ProviderError as e:
            return Response({"error": str(e)}, status=500)

        if data.get("status") != "success":
            return Response({"error": data}, status=400)

        # ZapOTP returns the received code in `sms_code` (null until it arrives).
        sms = (data.get("data") or {}).get("sms_code")
        if not sms:
            return Response({"message": "Waiting for SMS..."})

        with transaction.atomic():
            locked = VirtualNumber.objects.select_for_update().get(pk=vn.pk)

            # One row per distinct SMS — polling repeatedly won't duplicate it.
            ReceivedSMS.objects.get_or_create(virtual_number=locked, text=str(sms))

            if not locked.sms_received_at:
                locked.sms_received_at = timezone.now()
                locked.status = "Active"
                locked.save(update_fields=["sms_received_at", "status"])

            if not locked.charged:
                # Settle the hold reserved at purchase (always covered).
                wallet = Wallet.objects.select_for_update().get(user=request.user)
                cost = safe_decimal(locked.cost)
                wallet.balance = safe_decimal(wallet.balance) - cost
                wallet.reserved_balance = max(
                    Decimal("0"), safe_decimal(wallet.reserved_balance) - cost
                )
                wallet.save(update_fields=["balance", "reserved_balance"])
                locked.charged = True
                locked.save(update_fields=["charged"])

        invalidate_user_wallet_caches(request.user.id)
        return Response({"sms": sms})


class NumberHistoryView(generics.ListAPIView):
    serializer_class = VirtualNumberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            VirtualNumber.objects.filter(user=self.request.user)
            .prefetch_related("messages")
            .order_by("-created_at")
        )
