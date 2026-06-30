import os
import json
import requests
from decimal import Decimal
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import permissions, status
from django.views.decorators.csrf import csrf_exempt
from .models import Deposit
from django.db import transaction
from django.db.models import F, Sum, Count, Q
from virtualnumbers.models import VirtualNumber
from boost.models import BoostRequest
from django.contrib.auth import get_user_model
from common.cache_keys import admin_dashboard_stats_key, admin_users_key, user_profile_key, user_summary_key, user_transactions_key,admin_deposits_key
from common.cache_utils import delete_cache_keys,get_or_set_cache

from .services.whatsapp import send_admin_whatsapp
from .utils import verify_paystack_signature
from users.services import credit as wallet_credit
from common.fx import convert, FxError
from common.currencies import quantize

# Paystack settles in NGN, so we always CHARGE in NGN — but the user funds in
# their own wallet currency. We convert their amount to the NGN to charge, and
# credit their wallet in their own currency. (NGN users: convert is identity.)
DEPOSIT_MIN_NGN = Decimal("1000")
SERVICE_UNAVAILABLE = "This service is temporarily unavailable. Please try again later."


def _wallet_currency(user):
    w = getattr(user, "wallet", None)
    return (getattr(w, "currency", None) or "NGN") if w else "NGN"

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

User = get_user_model()


def _invalidate_deposit_caches(user_id):
    delete_cache_keys(
        admin_deposits_key(),
        admin_dashboard_stats_key(),
        user_transactions_key(user_id),
        user_profile_key(user_id),
        user_summary_key(user_id),
    )


def credit_deposit(deposit_id):
    """
    Credit a deposit's amount to its wallet EXACTLY ONCE.

    The deposit row is locked (select_for_update) and the 'already paid' check
    happens inside the lock, so concurrent webhook + callback deliveries for the
    same deposit cannot double-credit. Returns True if it credited now.
    """
    with transaction.atomic():
        dep = Deposit.objects.select_for_update().get(pk=deposit_id)
        if dep.status == "paid":
            return False  # idempotent: already credited
        dep.status = "paid"
        dep.confirmed_at = timezone.now()
        dep.save(update_fields=["status", "confirmed_at"])
        wallet_credit(dep.user, dep.amount)
        user_id = dep.user_id
    _invalidate_deposit_caches(user_id)
    return True


def mark_deposit_failed(deposit_id):
    with transaction.atomic():
        dep = Deposit.objects.select_for_update().get(pk=deposit_id)
        if dep.status in ("paid", "failed"):
            return
        dep.status = "failed"
        dep.confirmed_at = timezone.now()
        dep.save(update_fields=["status", "confirmed_at"])
        user_id = dep.user_id
    _invalidate_deposit_caches(user_id)

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def manual_bank_transfer_payment_sent(request):
    user = request.user
    amount = request.data.get("amount")

    if not amount:
        return Response({"error": "Amount is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        dec_amount = Decimal(str(amount))
    except:
        return Response({"error": "Invalid amount"}, status=status.HTTP_400_BAD_REQUEST)

    if dec_amount < Decimal("1000"):
        return Response({"error": "Minimum deposit is ₦1000"}, status=status.HTTP_400_BAD_REQUEST)
    
    dep = Deposit.objects.create(
        user=user,
        amount=dec_amount,
        currency="NGN",
        method="manual_bank_transfer",
        status="pending",
        provider_payload={"source": "user_clicked_payment_sent"}
    )

    delete_cache_keys(
        user_transactions_key(user.id),
        user_profile_key(user.id),
        user_summary_key(user.id),
        admin_deposits_key(),
        admin_dashboard_stats_key(),
    )

    
    msg = (
        "NEW MANUAL BANK TRANSFER\n\n"
        f"User: {user.email}\n"
        f"Amount: NGN {dec_amount}\n"
        f"Deposit ID: {dep.id}\n"
        f"Time: {timezone.now()}\n\n"
        "Please verify payment and confirm in admin."
    )

    try:
        sid = send_admin_whatsapp(msg)
        dep.provider_payload = {**(dep.provider_payload or {}), "twilio_sid": sid}
        dep.save(update_fields=["provider_payload"])
    except Exception as e:
        
        return Response({
            "deposit_id": str(dep.id),
            "status": dep.status,
            "warning": "Deposit created but WhatsApp notification failed",
            "details": str(e),
        }, status=200)

    return Response({
        "deposit_id": str(dep.id),
        "status": dep.status,
        "message": "Deposit created and admin notified",
        "twilio_sid": sid
    }, status=201)

@api_view(["POST"])
@permission_classes([permissions.IsAdminUser])
def admin_confirm_manual_deposit(request, pk):
    with transaction.atomic():
        dep = Deposit.objects.select_for_update().get(pk=pk)

        if dep.status == "paid":
            return Response({"message": "Already confirmed"}, status=200)

        if dep.status != "pending":
            return Response({"error": f"Cannot confirm deposit in '{dep.status}' state"}, status=400)

        dep.status = "paid"
        dep.confirmed_at = timezone.now()
        dep.save(update_fields=["status", "confirmed_at"])

        delete_cache_keys(
            admin_deposits_key(),
            admin_dashboard_stats_key(),
            user_transactions_key(dep.user.id),
            user_profile_key(dep.user.id),
            user_summary_key(dep.user.id),
        )

        wallet = dep.user.wallet
        wallet.balance = F("balance") + dep.amount
        wallet.save(update_fields=["balance"])

    return Response({"message": "Deposit confirmed and wallet credited"}, status=200)

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def create_deposit(request):
    user = request.user
    amount = request.data.get("amount")
    if not amount:
        return Response({"error": "Amount is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        dec_amount = Decimal(str(amount))
    except:
        return Response({"error": "Invalid amount"}, status=status.HTTP_400_BAD_REQUEST)

    # The amount is in the user's wallet currency. Convert it to the NGN we'll
    # actually charge through Paystack.
    wcur = _wallet_currency(user)
    try:
        charge_ngn = convert(dec_amount, wcur, "NGN")
    except FxError:
        return Response({"error": SERVICE_UNAVAILABLE}, status=503)

    if charge_ngn < DEPOSIT_MIN_NGN:
        min_wcur = quantize(convert(DEPOSIT_MIN_NGN, "NGN", wcur), wcur) if wcur != "NGN" else DEPOSIT_MIN_NGN
        return Response({"error": f"Minimum deposit is {min_wcur} {wcur}"},
                        status=status.HTTP_400_BAD_REQUEST)

    deposit = Deposit.objects.create(
        user=user,
        amount=dec_amount,          # credited to the wallet in ITS currency
        currency=wcur,
        method="paystack",
        status="pending",
        provider_payload={"charge_ngn": str(charge_ngn)},
    )

    delete_cache_keys(
        user_transactions_key(user.id),
        user_profile_key(user.id),
        user_summary_key(user.id),
        admin_deposits_key(),
        admin_dashboard_stats_key(),
    )

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "email": user.email,
        "amount": int(charge_ngn * 100),
        "currency": "NGN",
        "callback_url": f"{FRONTEND_URL}/deposit/callback?deposit_id={deposit.id}",
        "metadata": {"deposit_id": str(deposit.id), "user_id": str(user.id)}
    }
    r = requests.post("https://api.paystack.co/transaction/initialize", json=data, headers=headers)
    resp = r.json()
    if not resp.get("status"):
        deposit.status = "failed"
        deposit.save()
        return Response({"error": resp.get("message", "Failed to initialize payment")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    deposit.provider_payload = {**(deposit.provider_payload or {}), "init": resp}
    deposit.provider_reference = resp["data"]["reference"]
    deposit.save()
    return Response({
        "authorization_url": resp["data"]["authorization_url"],
        "reference": resp["data"]["reference"],
        "deposit_id": str(deposit.id),
        "balance": float(user.wallet.balance)
    })

@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def paystack_webhook(request):
    # 1) Verify the request really came from Paystack (HMAC-SHA512 of the raw body).
    raw_body = request.body  # must read raw bytes BEFORE parsing
    signature = request.META.get("HTTP_X_PAYSTACK_SIGNATURE")
    if not verify_paystack_signature(raw_body, signature):
        return Response(status=401)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Response(status=400)

    event = payload.get("event")
    data = payload.get("data", {})
    ref = data.get("reference")

    # Transfer events belong to CardPulse withdrawals, not deposits.
    if isinstance(event, str) and event.startswith("transfer."):
        from banking.services import handle_transfer_event
        handle_transfer_event(event, data)
        return Response(status=200)

    # Acknowledge (200) for anything we can't act on, so Paystack stops retrying.
    if not ref:
        return Response(status=200)

    try:
        dep = Deposit.objects.get(provider_reference=ref)
    except Deposit.DoesNotExist:
        return Response(status=200)
    except Deposit.MultipleObjectsReturned:
        dep = Deposit.objects.filter(provider_reference=ref).order_by("-created_at").first()

    if event == "charge.success":
        credit_deposit(dep.id)  # atomic, locked, idempotent
    elif event in ["charge.failed", "transfer.failed", "payment.failed"]:
        mark_deposit_failed(dep.id)

    return Response(status=200)

@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def deposit_callback(request):
  
    deposit_id = request.GET.get("deposit_id")
    if not deposit_id:
        return Response({"error": "Missing deposit ID"}, status=400)

    dep = get_object_or_404(Deposit, pk=deposit_id)

    
    if dep.status == "paid":
        print(dep.status)
        return redirect(f"{FRONTEND_URL}/deposit/success?deposit_id={dep.id}")
    if dep.status == "failed":
        return redirect(f"{FRONTEND_URL}/deposit/failed?deposit_id={dep.id}")

    
    reference = dep.provider_reference
    print(reference)
    if not reference:
        return redirect(f"{FRONTEND_URL}/deposit/pending?deposit_id={dep.id}")

    verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
    print(verify_url)
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
    try:
        r = requests.get(verify_url, headers=headers, timeout=15)
        resp = r.json()
    except Exception as e:
       
        return redirect(f"{FRONTEND_URL}/deposit/pending?deposit_id={dep.id}")

   
    data = resp.get("data") or {}
    status_text = data.get("status") 
    
    if status_text == "success":
        credit_deposit(dep.id)  # atomic, locked, idempotent — shared with the webhook
        return redirect(f"{FRONTEND_URL}/deposit/success?deposit_id={dep.id}&status=paid")
    elif status_text in ["failed", "abandoned", "cancelled"]:
        mark_deposit_failed(dep.id)
        redirect_url = f"{FRONTEND_URL}/deposit/failed?deposit_id={dep.id}&status=failed"
        return redirect(redirect_url)
    else:
        return redirect(f"{FRONTEND_URL}/deposit/pending?deposit_id={dep.id}&status=pending")
    

@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
def deposit_status(request, pk):
    dep = get_object_or_404(Deposit, pk=pk, user=request.user)

    if request.method == "POST":
        new_status = request.data.get("status")

        if new_status == "failed" and dep.status == "pending":
            dep.status = "failed"
            dep.confirmed_at = timezone.now()
            dep.save() 
            delete_cache_keys(
                admin_deposits_key(),
                admin_dashboard_stats_key(),
                user_transactions_key(dep.user.id),
                user_profile_key(dep.user.id),
                user_summary_key(dep.user.id),
            )
            return Response(
                {"message": "Deposit marked as failed due to timeout."},
                status=200
            )

        return Response(
            {"message": "No status change was needed."},
            status=200
        )
    return Response({
        "id": str(dep.id),
        "status": dep.status,
        "amount": float(dep.amount),
        "method": dep.method,
        "balance": float(dep.user.wallet.balance)
    })

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def transaction_history(request):
    user = request.user

    def fetch_transactions():
        deposits = Deposit.objects.filter(user=user).order_by("-created_at")
        return [
            {
                "id": str(dep.id),
                "amount": float(dep.amount),
                "method": dep.method,
                "status": dep.status,
                "created_at": dep.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for dep in deposits
        ]

    data = get_or_set_cache(user_transactions_key(user.id), fetch_transactions, timeout=180)
    return Response(data)

@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_list_users(request):
    # Not cached: online status must be live.
    from datetime import timedelta
    from django.utils import timezone
    from giftcards.models import GiftCardTrade, GiftCardSale

    online_cutoff = timezone.now() - timedelta(minutes=5)
    traded_ids = set(GiftCardTrade.objects.values_list("user_id", flat=True)) | \
        set(GiftCardSale.objects.values_list("user_id", flat=True))

    users = User.objects.all().order_by("-date_joined")
    data = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "full_name": getattr(u, "full_name", ""),
            "phone": getattr(u, "phone", None),
            "country": getattr(u, "country", None),
            "app": getattr(u, "app", "socialpulse"),
            "is_active": u.is_active,
            "is_staff": u.is_staff,
            "is_online": bool(u.last_seen and u.last_seen >= online_cutoff),
            "last_seen": u.last_seen.isoformat() if u.last_seen else None,
            "traded": u.id in traded_ids,
            "date_joined": u.date_joined.isoformat() if u.date_joined else None,
        }
        for u in users
    ]
    return Response(data, status=200)


@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_list_deposits(request):
    def fetch_deposits():
        deposits = Deposit.objects.select_related("user").all().order_by("-created_at")
        return [
            {
                "id": str(d.id),
                "user_email": d.user.email,
                "amount": float(d.amount),
                "currency": d.currency,
                "method": d.method,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "confirmed_at": d.confirmed_at.isoformat() if d.confirmed_at else None,
            }
            for d in deposits
        ]

    data = get_or_set_cache(admin_deposits_key(), fetch_deposits, timeout=180)
    return Response(data, status=200)

@api_view(["POST"])
@permission_classes([permissions.IsAdminUser])
def admin_reject_manual_deposit(request, pk):
    reason = request.data.get("reason", "Rejected by admin")

    dep = get_object_or_404(Deposit, pk=pk)

    if dep.status != "pending":
        return Response({"error": f"Cannot reject deposit in '{dep.status}' state"}, status=400)

    dep.status = "failed"
    dep.confirmed_at = timezone.now()
    payload = dep.provider_payload or {}
    payload["reject_reason"] = reason
    dep.provider_payload = payload
    dep.save(update_fields=["status", "confirmed_at", "provider_payload"])

    delete_cache_keys(
        admin_deposits_key(),
        admin_dashboard_stats_key(),
        user_transactions_key(dep.user.id),
        user_profile_key(dep.user.id),
        user_summary_key(dep.user.id),
    )

    return Response({"message": "Deposit rejected"}, status=200)


@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_list_numbers(request):
    """All virtual numbers across users, with method (API/Normal), status, and SMS state."""
    qs = VirtualNumber.objects.select_related("user").order_by("-created_at")

    status_f = request.query_params.get("status")
    source_f = request.query_params.get("source")  # "api" or "wallet"
    if status_f:
        qs = qs.filter(status=status_f)
    if source_f:
        qs = qs.filter(funding_source=source_f)

    data = [
        {
            "id": n.id,
            "user_email": n.user.email,
            "user_name": getattr(n.user, "full_name", ""),
            "phone_number": n.phone_number,
            "service": n.service,
            "country": n.country,
            "status": n.status,
            "method": "API" if n.funding_source == "api" else "Normal",
            "sms_received": bool(n.sms_received_at),
            "charged": n.charged,
            "cost": float(n.cost),
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "cancelled_at": n.cancelled_at.isoformat() if n.cancelled_at else None,
            "activation_id": n.activation_id,
        }
        for n in qs[:500]
    ]
    return Response(data, status=200)


@api_view(["GET"])
@permission_classes([permissions.IsAdminUser])
def admin_overview(request):
    """Aggregated platform stats for the admin dashboard."""
    numbers = VirtualNumber.objects.all()
    by_status = {row["status"]: row["n"] for row in numbers.values("status").annotate(n=Count("id"))}

    deposits = Deposit.objects.all()
    boosts = BoostRequest.objects.all()

    from datetime import timedelta
    online_cutoff = timezone.now() - timedelta(minutes=5)

    return Response({
        "users": User.objects.count(),
        "users_online": User.objects.filter(last_seen__gte=online_cutoff).count(),
        "numbers": {
            "total": numbers.count(),
            # "sold" = successfully purchased numbers, excluding cancelled/failed.
            "sold": numbers.exclude(status__in=["Cancelled", "Failed"]).count(),
            "api": numbers.filter(funding_source="api").count(),
            "normal": numbers.filter(funding_source="wallet").count(),
            "sms_received": numbers.filter(sms_received_at__isnull=False).count(),
            "cancelled": by_status.get("Cancelled", 0),
            "pending": by_status.get("Pending", 0),
            "active": by_status.get("Active", 0),
            "expired": by_status.get("Expired", 0),
            "revenue": float(numbers.filter(charged=True).aggregate(t=Sum("cost"))["t"] or 0),
        },
        "deposits": {
            "total": deposits.count(),
            "paid": deposits.filter(status="paid").count(),
            "pending": deposits.filter(status="pending").count(),
            "failed": deposits.filter(status="failed").count(),
            "volume": float(deposits.filter(status="paid").aggregate(t=Sum("amount"))["t"] or 0),
        },
        "boosts": {
            "total": boosts.count(),
            "processing": boosts.filter(status="Processing").count(),
            "failed": boosts.filter(status="Failed").count(),
        },
    }, status=200)