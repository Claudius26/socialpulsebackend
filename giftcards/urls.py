from django.urls import path

from .views import GiftcardCatalogView, GiftcardProductView, GiftcardCountriesView

app_name = "giftcards"

urlpatterns = [
    path("products/", GiftcardCatalogView.as_view(), name="catalog"),
    path("products/<int:product_id>/", GiftcardProductView.as_view(), name="product"),
    path("countries/", GiftcardCountriesView.as_view(), name="countries"),
]
