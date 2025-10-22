import os
import requests
from decimal import Decimal
from rest_framework import generics, permissions, status, views
from rest_framework.response import Response
from .models import BoostRequest
from .serializers import BoostRequestSerializer

SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL", "https://resellersmm.com/api/v2")

EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")

def get_live_usd_to_ngn_rate():
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/USD"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if response.status_code == 200 and "conversion_rates" in data:
            ngn_rate = Decimal(str(data["conversion_rates"].get("NGN", "1550.00")))
            return ngn_rate
        else:
            print("Invalid response from ExchangeRate API:", data)
            return Decimal("1550.00")
    except Exception as e:
        print("Error fetching exchange rate:", e)
        return Decimal("1550.00")

def fetch_all_smm_services():
    try:
        payload = {"key": SMM_API_KEY, "action": "services"}
        response = requests.post(SMM_API_URL, data=payload, timeout=20)
        return response.json() if response.status_code == 200 else []
    except Exception as e:
        print("Error fetching SMM services:", e)
        return []

def get_service_info(service_name, platform):
    try:
        payload = {"key": SMM_API_KEY, "action": "services"}
        response = requests.post(SMM_API_URL, data=payload, timeout=15)
        services_data = response.json()

        if not isinstance(services_data, list):
            return None

        name_match = service_name.lower()
        platform_match = platform.lower()

        for s in services_data:
            name = s.get("name", "").lower()
            if platform_match in name and name_match in name:
                return s
    except Exception as e:
        print("Error in get_service_info:", e)
    return None

class GetCategoryView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        platform = request.query_params.get("platform", "").lower()
        subcategory = request.query_params.get("subcategory", "").lower()
        if not platform or not subcategory:
            return Response({"error": "platform and subcategory are required"}, status=400)

        data = fetch_all_smm_services()
        if not isinstance(data, list):
            return Response({"error": "Invalid SMM API response"}, status=500)

        exchange_rate = get_live_usd_to_ngn_rate()
        matched_services = []

        for s in data:
            print(s.get("amount", ""))
            name = s.get("name", "").lower()
            if platform in name and subcategory in name:
                rate_usd = Decimal(str(s.get("rate", "0")))
                rate_with_profit_usd = rate_usd * Decimal("1.8")
                rate_ngn = rate_with_profit_usd * exchange_rate

                matched_services.append({
                    "service_id": s.get("service"),
                    "name": s.get("name"),
                    "category": s.get("category"),
                    "rate_usd": str(round(rate_with_profit_usd, 4)),
                    "rate_ngn": str(round(rate_ngn, 2)),
                    "min": s.get("min"),
                    "max": s.get("max"),
                    "refill": s.get("refill"),
                    "cancel": s.get("cancel"),
                    "duration": s.get("average_time") 
                })
               


        return Response(matched_services)

class GetServicePriceView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        service_id = request.query_params.get("service_id")
        quantity = request.query_params.get("quantity")
        if not service_id or not quantity:
            return Response({"error": "service_id and quantity are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = {"key": SMM_API_KEY, "action": "services"}
            response = requests.post(SMM_API_URL, data=payload, timeout=20)
            services_data = response.json()
            if not isinstance(services_data, list):
                return Response({"error": "Invalid response from SMM API"}, status=500)

            for s in services_data:
                if str(s["service"]) == str(service_id):
                    base_rate = Decimal(str(s["rate"]))
                    qty = Decimal(quantity)
                    total_usd = base_rate * qty / Decimal(1000)
                    total_with_profit_usd = total_usd * Decimal("1.8")
                    exchange_rate = get_live_usd_to_ngn_rate()
                    total_with_profit_ngn = total_with_profit_usd * exchange_rate
                    return Response({
                        "service": s["name"],
                        "base_rate_usd": str(base_rate),
                        "total_usd": str(round(total_usd, 4)),
                        "total_with_profit_usd": str(round(total_with_profit_usd, 4)),
                        "total_with_profit_ngn": str(round(total_with_profit_ngn, 2)),
                        "exchange_rate": str(exchange_rate),
                        "duration": s.get("average_time")  
                    }, status=status.HTTP_200_OK)

            return Response({"error": "Service not found"}, status=404)

        except Exception as e:
            return Response({"error": str(e)}, status=500)

class BoostRequestListCreateView(generics.ListCreateAPIView):
    serializer_class = BoostRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return BoostRequest.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        user = self.request.user
        wallet = user.wallet
        boost_request = serializer.save(user=user)

        try:
            payload = {"key": SMM_API_KEY, "action": "services"}
            response = requests.post(SMM_API_URL, data=payload, timeout=20)
            services_data = response.json()

            service_info = None
            service_id = None
            for s in services_data:
                name = s.get("name", "").lower()
                if boost_request.platform.lower() in name and boost_request.service.lower() in name:
                    service_info = s
                    service_id = s.get("service")
                    break

            if not service_info or not service_id:
                boost_request.status = "Failed"
                boost_request.error_message = "Service ID not found"
                boost_request.save()
                return

            boost_request.delivery_time = service_info.get("average_time")
            base_rate = Decimal(str(service_info.get("rate", "0")))
            qty = Decimal(boost_request.quantity)
            total_usd = base_rate * qty / Decimal(1000)
            total_with_profit_usd = total_usd * Decimal("1.3")
            exchange_rate = get_live_usd_to_ngn_rate()
            total_with_profit_ngn = total_with_profit_usd * exchange_rate
            boost_request.amount = round(total_with_profit_ngn, 2)
            boost_request.smm_charge = round(total_with_profit_usd, 4)
            boost_request.save()

            if wallet.balance < boost_request.amount:
                boost_request.status = "Failed"
                boost_request.error_message = "Insufficient wallet balance."
                boost_request.save()
                return

            wallet.balance -= boost_request.amount
            wallet.save()

            payload = {
                "key": SMM_API_KEY,
                "action": "add",
                "service": service_id,
                "link": boost_request.target,
                "quantity": boost_request.quantity,
            }
            response = requests.post(SMM_API_URL, data=payload, timeout=20)
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text}

            if "order" in data:
                boost_request.smm_order_id = data["order"]
                boost_request.status = "Processing"
                boost_request.error_message = None
            else:
                boost_request.status = "Failed"
                boost_request.error_message = data.get("error", "Unknown error from SMM provider")

            boost_request.save()

        except Exception as e:
            boost_request.status = "Failed"
            boost_request.error_message = f"Server error: {str(e)}"
            boost_request.save()

class BoostRequestStatusUpdateView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        order_id = request.data.get("order_id")
        if not order_id:
            return Response({"error": "order_id is required"}, status=400)

        try:
            boost_request = BoostRequest.objects.get(user=request.user, smm_order_id=order_id)
        except BoostRequest.DoesNotExist:
            return Response({"error": "Order not found"}, status=404)

        payload = {"key": SMM_API_KEY, "action": "status", "order": order_id}

        try:
            response = requests.post(SMM_API_URL, data=payload, timeout=15)
            data = response.json()

            boost_request.smm_charge = Decimal(str(data.get("charge", "0.0")))
            boost_request.smm_start_count = data.get("start_count")
            boost_request.smm_remains = data.get("remains")
            boost_request.smm_currency = data.get("currency", "USD")
            boost_request.status = data.get("status", "Processing").capitalize()
            boost_request.error_message = data.get("error")
            boost_request.delivery_time = data.get("average_time")  # update delivery time dynamically
            boost_request.save()

            return Response({"success": True, "status": boost_request.status, "details": data})
        except Exception as e:
            return Response({"error": str(e)}, status=500)
