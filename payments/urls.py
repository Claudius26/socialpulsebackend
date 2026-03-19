from django.urls import path

from .views import (
    create_deposit,
    paystack_webhook,
    deposit_status,
    deposit_callback,
    transaction_history,
    manual_bank_transfer_payment_sent,
    admin_confirm_manual_deposit,
    admin_list_users,
    admin_list_deposits,
    admin_reject_manual_deposit,
)

urlpatterns = [
    path("create/", create_deposit, name="create_deposit"),
    path("webhook/paystack/", paystack_webhook, name="paystack_webhook"),
    path("status/<uuid:pk>/", deposit_status, name="deposit_status"),
    path("callback/", deposit_callback, name="deposit_callback"),
    path("transactions/", transaction_history, name="transaction_history"),

    
    path("manual/payment-sent/", manual_bank_transfer_payment_sent, name="manual_payment_sent"),

    
    path("admin/users/", admin_list_users, name="admin_list_users"),
    path("admin/deposits/", admin_list_deposits, name="admin_list_deposits"),
    path("admin/manual/confirm/<uuid:pk>/", admin_confirm_manual_deposit, name="admin_confirm_manual"),
    path("admin/manual/reject/<uuid:pk>/", admin_reject_manual_deposit, name="admin_reject_manual"),
]