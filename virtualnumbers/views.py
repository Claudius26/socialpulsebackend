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


class GetServicesView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        service = normalize_service(request.query_params.get("service"))
        country = normalize_country(request.query_params.get("country"))

        if not service or not country:
            return Response({"error": "service and country are required"}, status=400)

        payload = {"country": country, "service": service}

        try:
            r = requests.get(
                f"{ZAPOTP_BASE_URL}/services.php",
                headers=ZAPOTP_HEADERS,
                params=payload,
                timeout=20,
            )
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                return Response(
                    {"error": "ZapOTP returned invalid JSON", "content": r.text},
                    status=500,
                )
        except requests.RequestException as e:
            return Response({"error": str(e)}, status=500)

        if data.get("status") != "success" or "data" not in data:
            return Response({"error": data}, status=400)

        profit = safe_decimal(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3))

        services = []
        for item in data.get("data", []):
            base_price = safe_decimal(item.get("price", 0))
            final_price = (base_price * (Decimal("1") + profit)).quantize(Decimal("0.01"))

            services.append(
                {
                    "pool_id": item.get("pool"),
                    "name": item.get("name"),
                    "success_rate": item.get("success_rate"),
                    "base_price": float(base_price),
                    "price_with_profit": float(final_price),
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
            r = requests.get(
                f"{ZAPOTP_BASE_URL}/services.php",
                headers=ZAPOTP_HEADERS,
                params={"country": country, "service": service},
                timeout=20,
            )
            r.raise_for_status()
            services_data = r.json()
        except Exception as e:
            return Response({"error": f"Failed to fetch latest pools: {str(e)}"}, status=500)

        if services_data.get("status") != "success" or "data" not in services_data:
            return Response({"error": services_data}, status=400)

        selected_pool = None
        for item in services_data.get("data", []):
            if str(item.get("pool")) == str(pool_id):
                selected_pool = item
                break

        if not selected_pool:
            return Response({"error": "Selected pool not found"}, status=400)

        base_price = safe_decimal(selected_pool.get("price", 0))
        profit = safe_decimal(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3))
        final_price = (base_price * (Decimal("1") + profit)).quantize(Decimal("0.01"))

        if wallet_available(wallet) < final_price:
            return Response({"error": "Insufficient wallet balance"}, status=400)

        payload = {
            "action": "rent",
            "service": service,
            "country": country,
            "pool": int(pool_id),
        }
        if provider in ("global", "usa"):
            payload["provider"] = provider

        try:
            r2 = requests.post(
                f"{ZAPOTP_BASE_URL}/orders.php",
                headers=ZAPOTP_HEADERS,
                json=payload,
                timeout=20,
            )
            r2.raise_for_status()
            order_data = r2.json()
        except Exception as e:
            return Response({"error": f"ZapOTP order failed: {str(e)}"}, status=500)

        if order_data.get("status") != "success":
            return Response({"error": order_data}, status=400)

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

        if vn.sms_received_at:
            return Response({"error": "You cannot cancel after SMS has been received"}, status=400)

        if vn.status in ("Cancelled", "Expired", "Failed"):
            return Response({"error": "This number is not cancellable"}, status=400)

        if minutes_since(vn.created_at) < 5:
            return Response({"error": "You can only cancel after 5 minutes."}, status=400)

        try:
            r = requests.post(
                ZAPOTP_CANCEL_URL,
                headers=ZAPOTP_HEADERS,
                json={"order_id": str(vn.activation_id)},
                timeout=20,
            )
            try:
                provider_resp = r.json()
            except Exception:
                provider_resp = {"raw": r.text}
        except Exception as e:
            return Response({"error": f"Cancel request failed: {str(e)}"}, status=500)

        if isinstance(provider_resp, dict) and provider_resp.get("status") != "success":
            return Response({"error": provider_resp}, status=400)

        with transaction.atomic():
            wallet = request.user.wallet
            reserved = safe_decimal(getattr(wallet, "reserved_balance", "0"))
            wallet.reserved_balance = max(Decimal("0"), reserved - safe_decimal(vn.cost))
            wallet.save()

            vn.status = "Cancelled"
            vn.cancelled_at = timezone.now()
            vn.save()

        return Response(
            {
                "success": True,
                "released_amount": float(vn.cost),
                "activation_id": vn.activation_id,
                "provider": provider_resp,
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
            r = requests.get(
                f"{ZAPOTP_BASE_URL}/sms.php",
                headers=ZAPOTP_HEADERS,
                params={"order_id": str(activation_id)},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return Response({"error": str(e)}, status=500)

        if data.get("status") != "success":
            return Response({"error": data}, status=400)

        sms = (data.get("data") or {}).get("sms")
        if not sms:
            return Response({"message": "Waiting for SMS..."})

        with transaction.atomic():
            ReceivedSMS.objects.create(virtual_number=vn, text=str(sms))

            if not vn.sms_received_at:
                vn.sms_received_at = timezone.now()
                vn.status = "Active"
                vn.save()

            if not vn.charged:
                wallet = request.user.wallet
                cost = safe_decimal(vn.cost)

                if wallet_available(wallet) < cost:
                    return Response({"error": "Insufficient wallet balance to finalize charge"}, status=400)

                wallet.balance = safe_decimal(wallet.balance) - cost
                wallet.reserved_balance = max(Decimal("0"), safe_decimal(getattr(wallet, "reserved_balance", "0")) - cost)
                wallet.save()

                vn.charged = True
                vn.save()

        return Response({"sms": sms})


class NumberHistoryView(generics.ListAPIView):
    serializer_class = VirtualNumberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return VirtualNumber.objects.filter(user=self.request.user).order_by("-created_at")
