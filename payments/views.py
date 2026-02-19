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

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

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
    if dec_amount < Decimal("1000"):
        return Response({"error": "Minimum deposit is ₦1000"}, status=status.HTTP_400_BAD_REQUEST)
    deposit = Deposit.objects.create(
        user=user,
        amount=dec_amount,
        currency="NGN",
        method="paystack",
        status="pending"
    )
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "email": user.email,
        "amount": int(dec_amount * 100),
        "callback_url": f"{FRONTEND_URL}/deposit/callback?deposit_id={deposit.id}",
        "metadata": {"deposit_id": str(deposit.id), "user_id": str(user.id)}
    }
    r = requests.post("https://api.paystack.co/transaction/initialize", json=data, headers=headers)
    resp = r.json()
    if not resp.get("status"):
        deposit.status = "failed"
        deposit.save()
        return Response({"error": resp.get("message", "Failed to initialize payment")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    deposit.provider_payload = resp
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
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except:
        return Response(status=400)
    event = payload.get("event")
    data = payload.get("data", {})
    ref = data.get("reference")
    if not ref:
        return Response(status=400)
    try:
        dep = Deposit.objects.get(provider_reference=ref)
    except Deposit.DoesNotExist:
        return Response(status=404)
    if event == "charge.success":
        if dep.status != "paid":
            dep.status = "paid"
            dep.confirmed_at = timezone.now()
            dep.save()
            wallet = dep.user.wallet
            wallet.balance += dep.amount
            wallet.save()
    elif event in ["charge.failed", "transfer.failed", "payment.failed"]:
        dep.status = "failed"
        dep.confirmed_at = timezone.now()
        dep.save()
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
        print('Payment successful.')
        if dep.status != "paid":
            dep.status = "paid"
            dep.confirmed_at = timezone.now()
            dep.save()
            wallet = dep.user.wallet
            wallet.balance += dep.amount
            wallet.save()
        return redirect(f"{FRONTEND_URL}/deposit/success?deposit_id={dep.id}&status=paid")
    elif status_text in ["failed", "abandoned", "cancelled"]:
        dep.status = "failed"
        dep.confirmed_at = timezone.now()
        dep.save()
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
    deposits = Deposit.objects.filter(user=user).order_by("-created_at")
    data = [
        {
            "id": str(dep.id),
            "amount": float(dep.amount),
            "method": dep.method,
            "status": dep.status,
            "created_at": dep.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for dep in deposits
    ]
    return Response(data)


# payments/views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import permissions, status
from django.db import transaction

from .models import BankTransferAccount
from .services.paystack import create_or_get_customer, create_dedicated_virtual_account

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def get_bank_transfer_account(request):
    user = request.user

    # if already created, return it
    existing = getattr(user, "bank_transfer_account", None)
    if existing and existing.account_number and existing.bank_name:
        return Response({
            "account_number": existing.account_number,
            "bank_name": existing.bank_name,
            "account_name": existing.account_name,
            "provider": existing.provider,
        })

    # create it
    with transaction.atomic():
        bta, _ = BankTransferAccount.objects.select_for_update().get_or_create(user=user)

        if not bta.customer_code:
            customer = create_or_get_customer(
                email=user.email,
                first_name=getattr(user, "first_name", "") or "",
                last_name=getattr(user, "last_name", "") or "",
            )
            bta.customer_code = customer.get("customer_code")
            bta.save()

        if not bta.account_number:
            dva = create_dedicated_virtual_account(bta.customer_code)
            bta.dedicated_account_id = str(dva.get("id"))
            bta.account_number = dva.get("account_number")
            bta.account_name = dva.get("account_name")
            bta.bank_name = (dva.get("bank") or {}).get("name")
            bta.save()

    return Response({
        "account_number": bta.account_number,
        "bank_name": bta.bank_name,
        "account_name": bta.account_name,
        "provider": bta.provider,
    }, status=status.HTTP_200_OK)


# payments/views.py
import json
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions
from rest_framework.response import Response

from .models import Deposit, BankTransferAccount
from .utils import verify_paystack_signature

@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def paystack_webhook(request):
    raw_body = request.body
    signature = request.headers.get("x-paystack-signature")

    if not verify_paystack_signature(raw_body, signature):
        return Response(status=400)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except:
        return Response(status=400)

    event = payload.get("event")
    data = payload.get("data", {}) or {}
    reference = data.get("reference")

    # We only care about successful payment
    if event != "charge.success" or not reference:
        return Response(status=200)

    # amount is kobo on Paystack
    amount_kobo = data.get("amount")
    currency = data.get("currency", "NGN")

    customer = data.get("customer", {}) or {}
    customer_code = customer.get("customer_code")

    if not amount_kobo or currency != "NGN" or not customer_code:
        return Response(status=200)

    amount_ngn = (Decimal(str(amount_kobo)) / Decimal("100")).quantize(Decimal("0.01"))

    # Idempotent: same reference should not credit twice
    with transaction.atomic():
        # If deposit already exists and paid, exit
        dep = Deposit.objects.select_for_update().filter(provider_reference=reference).first()
        if dep and dep.status == "paid":
            return Response(status=200)

        # Find which user owns this customer_code
        bta = BankTransferAccount.objects.select_for_update().filter(customer_code=customer_code).select_related("user").first()
        if not bta:
            # Unknown customer; ignore (or log)
            return Response(status=200)

        user = bta.user

        # Create or update deposit record
        if not dep:
            dep = Deposit.objects.create(
                user=user,
                amount=amount_ngn,
                currency="NGN",
                method="paystack",
                channel="bank_transfer",
                provider_reference=reference,
                provider_payload=payload,
                status="paid",
                confirmed_at=timezone.now()
            )
        else:
            dep.amount = amount_ngn
            dep.currency = "NGN"
            dep.status = "paid"
            dep.confirmed_at = timezone.now()
            dep.provider_payload = payload
            dep.save()

        # Credit wallet atomically (no race)
        user.wallet.balance = F("balance") + amount_ngn
        user.wallet.save(update_fields=["balance"])

    return Response(status=200)

