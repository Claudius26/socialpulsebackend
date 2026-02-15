from django.urls import path
from .views import (
    GetServicesView,
    PurchaseNumberView,
    CancelNumberView,
    GetSMSView,
     NumberHistoryView,
)

urlpatterns = [
    path("services/", GetServicesView.as_view(), name="get_services"),
    path("purchase/", PurchaseNumberView.as_view(), name="purchase_number"),
    path("cancel/", CancelNumberView.as_view(), name="cancel_number"),
    path("sms/<str:activation_id>/", GetSMSView.as_view(), name="get_sms"),
    path("history/", NumberHistoryView.as_view(), name="number_history"), 
]
