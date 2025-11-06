import os
import requests
from decimal import Decimal
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from .models import VirtualNumber, ReceivedSMS
from .serializers import VirtualNumberSerializer
from django.conf import settings
from dotenv import load_dotenv
load_dotenv()

SMS_API_KEY = os.getenv("SMS_ACTIVATE_API_KEY")
BASE_URL = "https://api.sms-activate.ae/stubs/handler_api.php"
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
EXCHANGE_RATE_URL = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/USD"

SERVICE_NAMES = {
    "fb": "Facebook", "ig": "Instagram", "wa": "WhatsApp", "tg": "Telegram",
    "go": "Google", "tw": "Twitter / X", "vk": "VKontakte", "ok": "Odnoklassniki",
    "am": "Amazon", "yt": "YouTube", "tt": "TikTok", "ma": "Mail.ru", "sn": "Snapchat",
    "gm": "Gmail", "ya": "Yahoo", "li": "LinkedIn", "qq": "QQ", "vi": "Viber",
    "nf": "Netflix", "pf": "PayPal", "dr": "Discord", "tn": "Tinder", "im": "IMO",
    "wb": "WeChat", "oi": "OpenAI", "tx": "TextNow", "kt": "KakaoTalk",
    "lf": "Line", "mb": "MercadoLibre", "mt": "Meta", "sg": "Signal", "bfv": "Bumble"
}

SERVICE_FULL_TO_CODE = {v.lower(): k for k, v in SERVICE_NAMES.items()}

COUNTRY_ID_TO_NAME = {}

def safe_decimal(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")

def get_countries():
    global COUNTRY_ID_TO_NAME
    if COUNTRY_ID_TO_NAME:
        return COUNTRY_ID_TO_NAME
    url = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getCountries"
    try:
        r = requests.get(url, timeout=15)
        data = r.json() if r.text else {}
        for cid, name in data.items():
            COUNTRY_ID_TO_NAME[str(cid)] = name
        return COUNTRY_ID_TO_NAME
    except Exception:
        return {}

def get_exchange_rates():
    try:
        r = requests.get(EXCHANGE_RATE_URL, timeout=15)
        data = r.json()
        if data.get("result") == "success":
            return data.get("conversion_rates", {})
    except Exception:
        pass
    return {"USD": 1}

class GetServicesView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        country = request.query_params.get("country", "0")
        url = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getNumbersStatus&country={country}"
        try:
            r = requests.get(url, timeout=15)
            text = r.text.strip()
            if not text:
                return Response({"services": []})
            try:
                data = r.json()
            except Exception:
                return Response({"services": []})
            services = []
            for code, available in data.items():
                try:
                    services.append({
                        "code": code,
                        "name": SERVICE_NAMES.get(code, code.upper()),
                        "available": int(available)
                    })
                except Exception:
                    continue
            services.sort(key=lambda x: x["available"], reverse=True)
            return Response({"country": country, "services": services})
        except Exception as e:
            return Response({"error": str(e)}, status=400)

class GetTopCountriesByServiceView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        service_input = request.query_params.get("service")
        if not service_input:
            return Response({"error": "service parameter is required"}, status=400)

        service_code = SERVICE_FULL_TO_CODE.get(service_input.lower())
        if not service_code:
            return Response({"error": f"Service '{service_input}' not recognized"}, status=400)

        user = request.user
        wallet_currency = getattr(user.wallet, "currency", "USD")
        countries = get_countries()
        exchange_rates = get_exchange_rates()
        rate = exchange_rates.get(wallet_currency, 1)

        profit_margin = Decimal(str(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 0.8)))

        url = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getTopCountriesByService&service={service_code}"
        try:
            r = requests.get(url, timeout=15)
            data = r.json() if r.text else {}
            out = []
            for entry in data.values():
                try:
                    country_id = str(entry.get("country"))
                    price_usd = safe_decimal(entry.get("price"))
                    price_with_profit_usd = (price_usd * (Decimal("1.0") + profit_margin)).quantize(Decimal("0.0001"))
                    price_local = (price_usd * Decimal(rate)).quantize(Decimal("0.0001"))
                    price_with_profit_local = (price_with_profit_usd * Decimal(rate)).quantize(Decimal("0.0001"))

                    out.append({
                        "country_id": country_id,
                        "country_name": countries.get(country_id, f"Country {country_id}"),
                        "base_price_usd": float(price_usd),
                        "price_with_profit_usd": float(price_with_profit_usd),
                        "base_price_local": float(price_local),
                        "price_with_profit_local": float(price_with_profit_local),
                        "local_currency": wallet_currency,
                        "count": int(entry.get("count", 0))
                    })
                except Exception:
                    continue

            out.sort(key=lambda x: x["count"], reverse=True)
            return Response({
                "service": SERVICE_NAMES.get(service_code, service_code.upper()),
                "countries": out
            })
        except Exception as e:
            return Response({"error": str(e)}, status=400)

class PurchaseNumberView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        wallet = user.wallet
        service_input = request.data.get("service")
        country_input = request.data.get("country")
        if not service_input or not country_input:
            return Response({"error": "service and country are required"}, status=400)

        service_code = SERVICE_FULL_TO_CODE.get(service_input.lower())
        if not service_code:
            return Response({"error": f"Service '{service_input}' not recognized"}, status=400)

        countries = get_countries()
        country_id = None
        for cid, cname in countries.items():
            if isinstance(cname, dict):
                cname_list = [cname.get("eng","").lower(), cname.get("rus","").lower(), cname.get("chn","").lower()]
                if country_input.lower() in cname_list:
                    country_id = cid
                    break
            else:
                if cname.lower() == country_input.lower():
                    country_id = cid
                    break

        if not country_id:
            return Response({"error": f"Country '{country_input}' not recognized"}, status=400)

        wallet_currency = getattr(wallet, "currency", "USD")
        exchange_rates = get_exchange_rates()
        rate = exchange_rates.get(wallet_currency, 1)

        profit_margin = Decimal(str(getattr(settings, "VIRTUALNUMBER_PROFIT_MARGIN", 5.0)))

        url = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getTopCountriesByService&service={service_code}"
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            country_entry = next((c for c in data.values() if str(c.get("country")) == str(country_id)), None)
            if not country_entry:
                return Response({"error": "country not available for this service"}, status=400)

            price_usd = safe_decimal(country_entry.get("price"))
            price_with_profit_usd = (price_usd * (Decimal("1.0") + profit_margin)).quantize(Decimal("0.0001"))
            cost_local = (price_with_profit_usd * Decimal(rate)).quantize(Decimal("0.0001"))
        except Exception:
            cost_local = Decimal("0.15")

        if wallet.balance < cost_local:
            return Response({"error": "Insufficient wallet balance"}, status=400)

        url2 = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getNumber&service={service_code}&country={country_id}"
        try:
            r2 = requests.get(url2, timeout=15)
            text = r2.text.strip()
        except Exception as e:
            return Response({"error": str(e)}, status=500)

        if not text.startswith("ACCESS_NUMBER"):
            return Response({"error": text}, status=500)

        parts = text.split(":")
        if len(parts) < 3:
            return Response({"error": "invalid provider response"}, status=500)

        activation_id = parts[1]
        phone_number = parts[2]
        wallet.balance = Decimal(wallet.balance) - cost_local
        wallet.save()

        v = VirtualNumber.objects.create(
            user=user,
            country=countries.get(str(country_id), f"Country {country_id}"),
            service=service_code,
            phone_number=phone_number,
            activation_id=activation_id,
            cost=cost_local,
            status="Active"
        )
        return Response(VirtualNumberSerializer(v).data, status=201)

class GetSMSView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, activation_id):
        url = f"{BASE_URL}?api_key={SMS_API_KEY}&action=getStatus&id={activation_id}"
        try:
            r = requests.get(url, timeout=15)
            text = r.text.strip()
        except Exception as e:
            return Response({"error": str(e)}, status=400)

        if "STATUS_OK" in text:
            try:
                _, sms_text = text.split(":", 1)
            except Exception:
                sms_text = text
            number = VirtualNumber.objects.filter(activation_id=activation_id).first()
            if number:
                ReceivedSMS.objects.create(virtual_number=number, text=sms_text)
            return Response({"sms": sms_text})

        if "STATUS_WAIT_CODE" in text:
            return Response({"message": "Waiting for SMS..."})
        return Response({"error": text}, status=400)
    
class NumberHistoryView(generics.ListAPIView):
    serializer_class = VirtualNumberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return VirtualNumber.objects.filter(
            user=self.request.user
        ).order_by("-created_at")
