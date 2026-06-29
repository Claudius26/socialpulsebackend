"""
Developer-API number operations. Mirrors the in-app virtual-number flow but
spends the separate API credit pool (api_balance) and tags numbers as
funding_source="api" so charge/release settle the right pool.
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.providers import get_otp_provider, ProviderError
from virtualnumbers.models import VirtualNumber, ReceivedSMS
from users.services import (
    reserve_api, charge_api, release_api, api_available, InsufficientFunds,
)


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _margin() -> Decimal:
    # API uses its own (lower) margin so developers get a wholesale rate.
    return Decimal(str(getattr(settings, "API_PROFIT_MARGIN", 0.40)))


def _price(base) -> Decimal:
    return (Decimal(str(base)) * (Decimal("1") + _margin())).quantize(Decimal("0.01"))


def list_pools(country, service):
    country = (country or "").strip().upper()
    service = (service or "").strip().lower()
    if not country or not service:
        raise ApiError("country and service are required")
    try:
        data = get_otp_provider().list_pools(country, service)
    except ProviderError as e:
        raise ApiError(str(e), 502)
    if not isinstance(data, dict) or data.get("status") != "success":
        raise ApiError("Provider error", 502)
    pools = [
        {
            "pool_id": item.get("pool"),
            "name": item.get("name"),
            "success_rate": item.get("success_rate"),
            "price": float(_price(item.get("price", 0))),
        }
        for item in data.get("data", [])
    ]
    return {"country": country, "service": service, "numbers": pools}


def purchase_number(user, service, country, pool_id):
    service = (service or "").strip().lower()
    country = (country or "").strip().upper()
    if not service or not country or not pool_id:
        raise ApiError("service, country and pool_id are required")

    try:
        data = get_otp_provider().list_pools(country, service)
    except ProviderError as e:
        raise ApiError(str(e), 502)
    pool = next((p for p in (data.get("data") or []) if str(p.get("pool")) == str(pool_id)), None)
    if not pool:
        raise ApiError("Pool not found")

    price = _price(pool.get("price", 0))

    # Hold the credit first, so we never rent without being able to pay.
    try:
        reserve_api(user, price)
    except InsufficientFunds:
        raise ApiError("Insufficient API credit", 402)

    try:
        order = get_otp_provider().rent(country, service, pool_id, None)
    except ProviderError as e:
        release_api(user, price)
        raise ApiError(f"Provider request failed: {e}", 502)

    od = order.get("data") or {}
    if not isinstance(order, dict) or order.get("status") != "success" or not od.get("order_id"):
        release_api(user, price)
        raise ApiError("Provider could not allocate a number", 502)

    vn = VirtualNumber.objects.create(
        user=user, country=country, service=service,
        phone_number=str(od.get("number")), activation_id=str(od.get("order_id")),
        cost=price, status="Pending", charged=False, funding_source="api",
    )
    return vn


def _get_api_number(user, activation_id):
    return VirtualNumber.objects.filter(
        user=user, activation_id=str(activation_id), funding_source="api"
    ).first()


def get_sms(user, activation_id):
    vn = _get_api_number(user, activation_id)
    if not vn:
        raise ApiError("Number not found", 404)
    if vn.status in ("Cancelled", "Expired", "Failed"):
        raise ApiError("This number is not active")

    try:
        data = get_otp_provider().get_sms(activation_id)
    except ProviderError as e:
        raise ApiError(str(e), 502)
    if not isinstance(data, dict) or data.get("status") != "success":
        raise ApiError("Provider error", 502)

    sms = (data.get("data") or {}).get("sms_code")
    if not sms:
        return {"status": "pending", "sms": None}

    with transaction.atomic():
        locked = VirtualNumber.objects.select_for_update().get(pk=vn.pk)
        ReceivedSMS.objects.get_or_create(virtual_number=locked, text=str(sms))
        if not locked.sms_received_at:
            locked.sms_received_at = timezone.now()
            locked.status = "Active"
            locked.save(update_fields=["sms_received_at", "status"])
        if not locked.charged:
            charge_api(user, locked.cost)
            locked.charged = True
            locked.save(update_fields=["charged"])

    return {"status": "received", "sms": str(sms)}


def cancel_number(user, activation_id):
    vn = _get_api_number(user, activation_id)
    if not vn:
        raise ApiError("Number not found", 404)
    if vn.sms_received_at:
        raise ApiError("Cannot cancel after an SMS has been received")
    if vn.status in ("Cancelled", "Expired", "Failed"):
        raise ApiError("This number is not cancellable")

    # ZapOTP has no cancel endpoint — cancellation is internal: release the held
    # API credit and mark it cancelled. The number expires on the provider side.
    with transaction.atomic():
        locked = VirtualNumber.objects.select_for_update().get(pk=vn.pk)
        if locked.status != "Cancelled":
            release_api(user, locked.cost)
            locked.status = "Cancelled"
            locked.cancelled_at = timezone.now()
            locked.save(update_fields=["status", "cancelled_at"])
    return vn
