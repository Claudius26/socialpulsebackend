from django.urls import path

from .views import (
    BankListView, ResolveAccountView, InitiateWithdrawalView, MyWithdrawalsView, DepositInitView,
)

app_name = "banking"

urlpatterns = [
    path("banks/", BankListView.as_view(), name="banks"),
    path("resolve-account/", ResolveAccountView.as_view(), name="resolve-account"),
    path("withdraw/", InitiateWithdrawalView.as_view(), name="withdraw"),
    path("withdrawals/", MyWithdrawalsView.as_view(), name="withdrawals"),
    path("deposit/", DepositInitView.as_view(), name="deposit"),
]
