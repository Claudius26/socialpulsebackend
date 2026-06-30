from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from . import services

User = get_user_model()

FIXED_PRODUCT = {
    "productId": 1,
    "productName": "Amazon US",
    "denominationType": "FIXED",
    "recipientCurrencyCode": "USD",
    "fixedRecipientDenominations": [10, 25, 50],
    "logoUrls": ["http://logo/amazon.png"],
    "brand": {"brandName": "Amazon"},
    "country": {"isoName": "US", "name": "United States", "flagUrl": "http://flag/us.png"},
    "redeemInstruction": {"concise": "Redeem at amazon.com"},
}

RANGE_PRODUCT = {
    "productId": 2,
    "productName": "Razer Gold",
    "denominationType": "RANGE",
    "recipientCurrencyCode": "USD",
    "minRecipientDenomination": 5,
    "maxRecipientDenomination": 100,
    "logoUrls": [],
    "brand": {"brandName": "Razer"},
    "country": {"isoName": "US", "name": "United States", "flagUrl": "http://flag/us.png"},
    "redeemInstruction": {"concise": ""},
}


class FakeProvider:
    def list_products(self, country=None, page=1, size=50, product_name=None):
        return {"content": [FIXED_PRODUCT, RANGE_PRODUCT], "pageNumber": page,
                "totalPages": 1, "totalElements": 2}

    def get_product(self, product_id):
        return FIXED_PRODUCT if int(product_id) == 1 else RANGE_PRODUCT

    def list_countries(self):
        return [{"isoName": "US", "name": "United States", "flagUrl": "http://flag/us.png",
                 "currencyCode": "USD"}]


def make_cardpulse_user(email="cp@cardpulse.test", tag="cp"):
    u = User(email=email, username=email, full_name="CP", app=User.APP_CARDPULSE, tag=tag)
    u.set_password("StrongPass123")
    u.save()
    return u


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


class CatalogServiceTests(APITestCase):
    def setUp(self):
        cache.clear()

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_normalize_fixed_product_prices_in_ngn(self, _fx):
        entry = services.normalize_product(FIXED_PRODUCT)
        self.assertEqual(entry["brand"], "Amazon")
        self.assertEqual(entry["currency"], "USD")
        self.assertEqual(len(entry["denominations"]), 3)
        # 10 USD * 1600 * (1 + 0 markup) = 16000
        self.assertEqual(entry["denominations"][0], {"value": 10.0, "price_ngn": 16000.0})

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_normalize_range_product(self, _fx):
        entry = services.normalize_product(RANGE_PRODUCT)
        self.assertEqual(entry["denomination_type"], "RANGE")
        self.assertEqual(entry["range"]["min"], 5.0)
        self.assertEqual(entry["range"]["min_price_ngn"], 8000.0)
        self.assertEqual(entry["range"]["max_price_ngn"], 160000.0)

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_normalized_entry_hides_margin_and_fx(self, _fx):
        entry = services.normalize_product(FIXED_PRODUCT)
        # The client must never see the rate or markup — only final NGN prices.
        self.assertNotIn("rate", entry)
        self.assertNotIn("markup", entry)
        self.assertNotIn("buy_markup_rate", entry)


class CatalogEndpointTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_cardpulse_user()

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeProvider())
    def test_catalog_returns_products(self, _prov, _fx):
        res = self.client.get(reverse("giftcards:catalog"), HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data["products"]), 2)
        self.assertEqual(res.data["products"][0]["name"], "Amazon US")

    def test_catalog_requires_auth(self):
        res = self.client.get(reverse("giftcards:catalog"))
        self.assertIn(res.status_code, (401, 403))

    def test_catalog_rejects_socialpulse_user(self):
        web = User(email="web@socialpulse.test", username="web@socialpulse.test",
                   full_name="Web", app=User.APP_SOCIALPULSE)
        web.set_password("StrongPass123")
        web.save()
        res = self.client.get(reverse("giftcards:catalog"), HTTP_AUTHORIZATION=auth(web))
        self.assertEqual(res.status_code, 403)

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("giftcards.views.get_giftcard_provider", return_value=FakeProvider())
    def test_product_detail(self, _prov, _fx):
        res = self.client.get(
            reverse("giftcards:product", args=[1]), HTTP_AUTHORIZATION=auth(self.user)
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["product_id"], 1)

    @patch("giftcards.views.get_giftcard_provider", return_value=FakeProvider())
    def test_countries(self, _prov):
        res = self.client.get(
            reverse("giftcards:countries"), HTTP_AUTHORIZATION=auth(self.user)
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["countries"][0]["iso"], "US")
