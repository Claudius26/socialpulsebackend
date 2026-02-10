import os
import requests
from decimal import Decimal
from dotenv import load_dotenv

from django.conf import settings
from rest_framework import generics, permissions
from rest_framework.response import Response

from .models import VirtualNumber, ReceivedSMS
from .serializers import VirtualNumberSerializer

load_dotenv()

ZAPOTP_API_KEY = os.getenv("ZAPOTP_API_KEY")
ZAPOTP_BASE_URL = "https://zapotp.com/account/api/v1"

ZAPOTP_HEADERS = {
    "Authorization": f"Bearer {ZAPOTP_API_KEY}",
    "Content-Type": "application/json",
}

def safe_decimal(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


class GetServicesView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        service = request.query_params.get("service")  
        country = request.query_params.get("country") 

        if not country:
            return Response({"error": "country is required"}, status=400)

        payload = {"country": country}
        if service:
            payload["service"] = service

        try:
            r = requests.get(
                f"{ZAPOTP_BASE_URL}/services.php",
                headers=ZAPOTP_HEADERS,
                params=payload,
                timeout=15
            )
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                return Response({"error": "ZapOTP returned invalid JSON", "content": r.text}, status=500)

        except requests.RequestException as e:
            return Response({"error": str(e)}, status=500)

        if data.get("status") != "success" or "data" not in data:
            return Response({"error": data}, status=400)

        profit = Decimal(str(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3)))
        services = []
        for item in data["data"]:
            base_price = Decimal(str(item.get("price", 0)))
            final_price = (base_price * (Decimal("1") + profit)).quantize(Decimal("0.0001"))
            services.append({
                "name": item.get("name"),
                "duration": item.get("duration"),
                "success_rate": item.get("success_rate"),
                "base_price": float(base_price),
                "price_with_profit": float(final_price),
                "available": item.get("count", 0),
                "currency": "NGN",
            })

        return Response({
            "services": services
        })

class PurchaseNumberView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        wallet = user.wallet

        service = request.data.get("service")
        country = request.data.get("country")

        if not service or not country:
            return Response(
                {"error": "service and country are required"},
                status=400
            )

        try:
            r = requests.post(
                f"{ZAPOTP_BASE_URL}/numbers",
                headers=ZAPOTP_HEADERS,
                json={
                    "service": service,
                    "country": country
                },
                timeout=15
            )
            data = r.json()
        except Exception as e:
            return Response({"error": str(e)}, status=500)

        if "number" not in data:
            return Response({"error": data}, status=400)

        base_price = safe_decimal(data.get("price", "0"))
        profit = Decimal(str(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.3)))
        final_price = (base_price * (Decimal("1") + profit)).quantize(
            Decimal("0.0001")
        )

        if wallet.balance < final_price:
            return Response({"error": "Insufficient wallet balance"}, status=400)

        wallet.balance -= final_price
        wallet.save()

        v = VirtualNumber.objects.create(
            user=user,
            country=country,
            service=service,
            phone_number=data["number"],
            activation_id=data["activation_id"],
            cost=final_price,
            status="Active"
        )

        return Response(VirtualNumberSerializer(v).data, status=201)

class GetSMSView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, activation_id):
        try:
            r = requests.get(
                f"{ZAPOTP_BASE_URL}/sms/{activation_id}",
                headers=ZAPOTP_HEADERS,
                timeout=15
            )
            data = r.json()
        except Exception as e:
            return Response({"error": str(e)}, status=500)

        if not data.get("sms"):
            return Response({"message": "Waiting for SMS..."})

        number = VirtualNumber.objects.filter(
            activation_id=activation_id
        ).first()

        if number:
            ReceivedSMS.objects.create(
                virtual_number=number,
                text=data["sms"]
            )

        return Response({"sms": data["sms"]})

class NumberHistoryView(generics.ListAPIView):
    serializer_class = VirtualNumberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return VirtualNumber.objects.filter(
            user=self.request.user
        ).order_by("-created_at")
