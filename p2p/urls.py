from django.urls import path

from .views import TagLookupView, SendCashView, SendGiftcardView, TransferHistoryView

app_name = "p2p"

urlpatterns = [
    path("lookup/", TagLookupView.as_view(), name="lookup"),
    path("send/cash/", SendCashView.as_view(), name="send-cash"),
    path("send/giftcard/", SendGiftcardView.as_view(), name="send-giftcard"),
    path("history/", TransferHistoryView.as_view(), name="history"),
]
