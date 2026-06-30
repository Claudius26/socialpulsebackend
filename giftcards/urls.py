from django.urls import path

from .views import (
    GiftcardCatalogView, GiftcardProductView, GiftcardCountriesView,
    PurchaseGiftcardView, MyGiftcardsView, GiftcardDetailView, RevealGiftcardView,
    TradeGiftcardView, MyTradesView, SubmitSaleView, MySalesView,
)

app_name = "giftcards"

urlpatterns = [
    path("products/", GiftcardCatalogView.as_view(), name="catalog"),
    path("products/<int:product_id>/", GiftcardProductView.as_view(), name="product"),
    path("countries/", GiftcardCountriesView.as_view(), name="countries"),
    path("buy/", PurchaseGiftcardView.as_view(), name="buy"),
    path("mine/", MyGiftcardsView.as_view(), name="mine"),
    path("mine/<int:pk>/", GiftcardDetailView.as_view(), name="detail"),
    path("mine/<int:pk>/reveal/", RevealGiftcardView.as_view(), name="reveal"),
    path("mine/<int:pk>/trade/", TradeGiftcardView.as_view(), name="trade"),
    path("trades/", MyTradesView.as_view(), name="trades"),
    path("sell/", SubmitSaleView.as_view(), name="sell"),
    path("sales/", MySalesView.as_view(), name="sales"),
]
