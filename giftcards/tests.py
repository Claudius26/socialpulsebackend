from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from common.providers import ProviderError
from cardpulse.crypto import decrypt
from cardpulse.models import LedgerEntry, ProfitEntry, RateConfig
from users.services import get_or_create_wallet

from . import services
from .models import GiftCard, GiftCardOrder, GiftCardTrade

User = get_user_model()

FIXED_PRODUCT = {
    "productId": 1,
    "productName": "Amazon US",
    "denominationType": "FIXED",
    "recipientCurrencyCode": "USD",
    "senderCurrencyCode": "USD",
    "fixedRecipientDenominations": [10, 25, 50],
    "fixedRecipientToSenderDenominationsMap": {"10": 10, "25": 25, "50": 50},
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


class FakeBuyProvider:
    def get_product(self, product_id):
        return FIXED_PRODUCT

    def order(self, product_id, unit_price, quantity=1, recipient_email=None, custom_identifier=None):
        return {"transactionId": 999, "status": "SUCCESSFUL", "amount": unit_price}

    def redeem_code(self, transaction_id):
        return [{"cardNumber": "1234-5678-9012", "pinCode": "4321"}]


class FailingBuyProvider(FakeBuyProvider):
    def order(self, *a, **k):
        raise ProviderError("provider down")


def fund(user, amount):
    w = get_or_create_wallet(user)
    w.balance = Decimal(str(amount))
    w.save(update_fields=["balance"])
    return w


class PurchaseTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_cardpulse_user("buyer@cardpulse.test", tag="buyer")
        fund(self.user, 50000)

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider())
    def test_successful_purchase(self, _prov, _fx):
        res = self.client.post(reverse("giftcards:buy"), {
            "product_id": 1, "face_value": "10", "idempotency_key": "key-success",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "completed")
        card = GiftCard.objects.get(owner=self.user)
        self.assertEqual(card.status, GiftCard.STATUS_OWNED)
        self.assertTrue(card.redeemable)
        # 10 USD * 1600 = 16000 debited -> 34000 left
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("34000.00"))
        self.assertTrue(LedgerEntry.objects.filter(
            user=self.user, kind="giftcard_purchase", direction="debit").exists())

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider())
    def test_insufficient_funds(self, _prov, _fx):
        fund(self.user, 0)
        res = self.client.post(reverse("giftcards:buy"), {
            "product_id": 1, "face_value": "10", "idempotency_key": "key-poor",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 402)
        self.assertFalse(GiftCard.objects.filter(owner=self.user).exists())

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider())
    def test_idempotency_does_not_double_charge(self, _prov, _fx):
        payload = {"product_id": 1, "face_value": "10", "idempotency_key": "same-key"}
        r1 = self.client.post(reverse("giftcards:buy"), payload, format="json",
                              HTTP_AUTHORIZATION=auth(self.user))
        r2 = self.client.post(reverse("giftcards:buy"), payload, format="json",
                              HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(r1.data["id"], r2.data["id"])
        self.assertEqual(GiftCardOrder.objects.filter(idempotency_key="same-key").count(), 1)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("34000.00"))  # charged once

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FailingBuyProvider())
    def test_provider_failure_refunds(self, _prov, _fx):
        res = self.client.post(reverse("giftcards:buy"), {
            "product_id": 1, "face_value": "10", "idempotency_key": "key-fail",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.data["status"], "failed")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("50000.00"))  # fully refunded
        self.assertTrue(LedgerEntry.objects.filter(
            user=self.user, kind="reversal", direction="credit").exists())

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider())
    def test_code_encrypted_at_rest(self, _prov, _fx):
        self.client.post(reverse("giftcards:buy"), {
            "product_id": 1, "face_value": "10", "idempotency_key": "key-enc",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        card = GiftCard.objects.get(owner=self.user)
        self.assertNotIn("1234-5678-9012", card.code_encrypted)
        self.assertEqual(decrypt(card.code_encrypted), "1234-5678-9012")

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider())
    def test_list_never_exposes_code(self, _prov, _fx):
        self.client.post(reverse("giftcards:buy"), {
            "product_id": 1, "face_value": "10", "idempotency_key": "key-list",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        res = self.client.get(reverse("giftcards:mine"), HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("code", res.data[0])
        self.assertNotIn("pin", res.data[0])
        self.assertNotIn("code_encrypted", res.data[0])


class RevealTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_cardpulse_user("rev@cardpulse.test", tag="revealer")
        self.user.set_transaction_pin("1234")
        self.user.save()
        fund(self.user, 50000)

    def _buy(self):
        with patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600")), \
             patch("common.providers.get_giftcard_provider", return_value=FakeBuyProvider()):
            self.client.post(reverse("giftcards:buy"), {
                "product_id": 1, "face_value": "10", "idempotency_key": "rev-key",
            }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        return GiftCard.objects.get(owner=self.user)

    def test_reveal_with_correct_pin(self):
        card = self._buy()
        res = self.client.post(reverse("giftcards:reveal", args=[card.id]), {"pin": "1234"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["code"], "1234-5678-9012")
        card.refresh_from_db()
        self.assertEqual(card.status, GiftCard.STATUS_REVEALED)
        self.assertFalse(card.redeemable)

    def test_reveal_wrong_pin_rejected(self):
        card = self._buy()
        res = self.client.post(reverse("giftcards:reveal", args=[card.id]), {"pin": "0000"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 403)
        card.refresh_from_db()
        self.assertTrue(card.redeemable)  # unchanged


def owned_card(owner):
    return GiftCard.objects.create(
        owner=owner, product_id=1, product_name="Amazon US", brand="Amazon",
        country="US", currency="USD", face_value=Decimal("10"),
        face_value_ngn=Decimal("16000"), cost_ngn=Decimal("16000"),
        code_encrypted="enc", pin_encrypted="enc", status=GiftCard.STATUS_OWNED,
        redeemable=True,
    )


class TradeTests(APITestCase):
    def setUp(self):
        cache.clear()
        RateConfig.objects.all().delete()  # default 0.90 payout, 0 threshold
        self.user = make_cardpulse_user("trader@cardpulse.test", tag="trader")
        self.user.set_transaction_pin("1234")
        self.user.save()
        get_or_create_wallet(self.user)

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_trade_pays_90_percent_and_banks_card(self, _fx):
        card = owned_card(self.user)
        res = self.client.post(reverse("giftcards:trade", args=[card.id]), {"pin": "1234"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        # 10 USD * 1600 = 16000 value; trader gets 90% = 14400
        self.assertEqual(Decimal(str(res.data["payout_ngn"])), Decimal("14400.00"))
        self.assertEqual(res.data["status"], "completed")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("14400.00"))
        card.refresh_from_db()
        self.assertIsNone(card.owner_id)               # back to inventory
        self.assertEqual(card.status, GiftCard.STATUS_TRADED)
        self.assertTrue(LedgerEntry.objects.filter(user=self.user, kind="trade_payout").exists())
        # platform keeps 10% = 1600
        self.assertTrue(ProfitEntry.objects.filter(source="trade", amount=Decimal("1600.00")).exists())

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_trade_response_hides_margin(self, _fx):
        card = owned_card(self.user)
        res = self.client.post(reverse("giftcards:trade", args=[card.id]), {"pin": "1234"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        for hidden in ("value_ngn", "profit_ngn", "payout_rate"):
            self.assertNotIn(hidden, res.data)

    def test_trade_wrong_pin(self):
        card = owned_card(self.user)
        res = self.client.post(reverse("giftcards:trade", args=[card.id]), {"pin": "0000"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 403)
        card.refresh_from_db()
        self.assertEqual(card.owner_id, self.user.id)

    def test_cannot_trade_revealed_card(self):
        card = owned_card(self.user)
        card.status = GiftCard.STATUS_REVEALED
        card.redeemable = False
        card.save()
        res = self.client.post(reverse("giftcards:trade", args=[card.id]), {"pin": "1234"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 400)

    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_above_threshold_goes_to_review_then_admin_approves(self, _fx):
        cfg = RateConfig.get_solo()
        cfg.manual_review_threshold = Decimal("10000")  # payout 14400 >= 10000
        cfg.save()
        card = owned_card(self.user)
        res = self.client.post(reverse("giftcards:trade", args=[card.id]), {"pin": "1234"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "pending_review")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("0.00"))  # not paid yet
        card.refresh_from_db()
        self.assertFalse(card.redeemable)  # locked

        admin = make_cardpulse_user("admin@cardpulse.test", tag="adminx")
        trade = GiftCardTrade.objects.get(user=self.user)
        services.approve_trade(admin, trade.id)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("14400.00"))
        card.refresh_from_db()
        self.assertIsNone(card.owner_id)
