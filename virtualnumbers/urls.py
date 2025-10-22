from django.urls import path
from .views import (
    GetServicesView,
    GetTopCountriesByServiceView,
    PurchaseNumberView,
    GetSMSView,
     NumberHistoryView,
)

urlpatterns = [
    path("services/", GetServicesView.as_view(), name="get_services"),
    path("countries/", GetTopCountriesByServiceView.as_view(), name="get_countries"),
    path("purchase/", PurchaseNumberView.as_view(), name="purchase_number"),
    path("sms/<str:activation_id>/", GetSMSView.as_view(), name="get_sms"),
    path("history/", NumberHistoryView.as_view(), name="number_history"), 
]
