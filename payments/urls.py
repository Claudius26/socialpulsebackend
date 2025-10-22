from django.urls import path
from .views import (
    create_deposit,
    paystack_webhook,
    deposit_status,
    deposit_callback,
    transaction_history,
)

urlpatterns = [
    path("create/", create_deposit, name="create_deposit"),
    path("webhook/paystack/", paystack_webhook, name="paystack_webhook"),
    path("status/<uuid:pk>/", deposit_status, name="deposit_status"),
    path("callback/", deposit_callback, name="deposit_callback"),
    path("transactions/", transaction_history, name="transaction_history"),
]
