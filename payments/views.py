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
from dotenv import load_dotenv

load_dotenv()

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
