from django.urls import path

from . import admin_views as v

app_name = "cardpulse_admin"

urlpatterns = [
    path("overview/", v.overview, name="overview"),
    path("users/", v.users_list, name="users"),
    path("inventory/", v.inventory, name="inventory"),
    path("trades/", v.trades_queue, name="trades"),
    path("trades/<int:pk>/approve/", v.trade_approve, name="trade-approve"),
    path("trades/<int:pk>/reject/", v.trade_reject, name="trade-reject"),
    path("sales/", v.sales_queue, name="sales"),
    path("sales/<int:pk>/approve/", v.sale_approve, name="sale-approve"),
    path("sales/<int:pk>/reject/", v.sale_reject, name="sale-reject"),
    path("withdrawals/", v.withdrawals_queue, name="withdrawals"),
    path("withdrawals/<int:pk>/approve/", v.withdrawal_approve, name="withdrawal-approve"),
    path("withdrawals/<int:pk>/reject/", v.withdrawal_reject, name="withdrawal-reject"),
    path("rates/", v.rates, name="rates"),
    path("profit/", v.profit, name="profit"),
    path("audit/", v.audit, name="audit"),
]
