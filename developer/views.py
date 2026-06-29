from decimal import Decimal, InvalidOperation

from rest_framework.decorators import (
    api_view, permission_classes, authentication_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ApiKey, generate_api_key
from .authentication import ApiKeyAuthentication
from . import services
from users.services import topup_api_credit, api_available, InsufficientFunds


def _serialize_key(k, full_key=None):
    data = {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "is_active": k.is_active,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
    }
    if full_key:
        data["key"] = full_key  # shown exactly once
    return data


# ======================================================================
# Dashboard endpoints — authenticated with the app's JWT (logged-in user)
# ======================================================================
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def api_keys(request):
    if request.method == "GET":
        keys = ApiKey.objects.filter(user=request.user)
        return Response([_serialize_key(k) for k in keys])

    name = (request.data.get("name") or "Default").strip()[:100] or "Default"
    full_key, prefix, key_hash = generate_api_key()
    key = ApiKey.objects.create(user=request.user, name=name, prefix=prefix, key_hash=key_hash)
    return Response(_serialize_key(key, full_key=full_key), status=201)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def revoke_api_key(request, pk):
    try:
        key = ApiKey.objects.get(pk=pk, user=request.user)
    except ApiKey.DoesNotExist:
        return Response({"error": "Key not found"}, status=404)
    key.is_active = False
    key.save(update_fields=["is_active"])
    return Response({"success": True})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_credit(request):
    w = request.user.wallet
    return Response({
        "api_balance": float(w.api_balance),
        "api_reserved": float(w.api_reserved_balance),
        "api_available": float(api_available(w)),
        "wallet_balance": float(w.balance),
        "currency": w.currency,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def topup_api_credit_view(request):
    try:
        amount = Decimal(str(request.data.get("amount")))
    except (InvalidOperation, TypeError):
        return Response({"error": "Invalid amount"}, status=400)
    if amount <= 0:
        return Response({"error": "Amount must be greater than zero"}, status=400)
    try:
        w = topup_api_credit(request.user, amount)
    except InsufficientFunds as e:
        return Response({"error": str(e)}, status=400)
    return Response({
        "api_balance": float(w.api_balance),
        "wallet_balance": float(w.balance),
    })


# ======================================================================
# Public API v1 — authenticated with an API key
# ======================================================================
def _err(e):
    return Response({"error": e.message}, status=e.status)


@api_view(["GET"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def v1_list_numbers(request):
    try:
        result = services.list_pools(
            request.query_params.get("country"), request.query_params.get("service")
        )
    except services.ApiError as e:
        return _err(e)
    return Response(result)


@api_view(["POST"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def v1_purchase(request):
    try:
        vn = services.purchase_number(
            request.user,
            request.data.get("service"),
            request.data.get("country"),
            request.data.get("pool_id"),
        )
    except services.ApiError as e:
        return _err(e)
    return Response({
        "activation_id": vn.activation_id,
        "number": vn.phone_number,
        "service": vn.service,
        "country": vn.country,
        "cost": float(vn.cost),
        "status": vn.status,
    }, status=201)


@api_view(["GET"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def v1_get_sms(request, activation_id):
    try:
        result = services.get_sms(request.user, activation_id)
    except services.ApiError as e:
        return _err(e)
    return Response(result)


@api_view(["POST"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def v1_cancel(request, activation_id):
    try:
        vn = services.cancel_number(request.user, activation_id)
    except services.ApiError as e:
        return _err(e)
    return Response({"activation_id": vn.activation_id, "status": vn.status})


@api_view(["GET"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def v1_balance(request):
    w = request.user.wallet
    return Response({
        "api_balance": float(w.api_balance),
        "api_available": float(api_available(w)),
        "currency": w.currency,
    })
