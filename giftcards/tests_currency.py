"""Per-currency giftcard money flows: a non-NGN wallet is charged / paid in its
own currency, while platform profit stays booked in NGN."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from cardpulse.models import LedgerEntry, ProfitEntry, RateConfig
from users.services import get_or_create_wallet

from . import services
from .models import GiftCard, GiftCardSale
from .tests import FakeProvider, FIXED_PRODUCT

User = get_user_model()


def _ghs_user(email, tag):
    u = User(email=email, username=email, full_name="GH",
             app=User.APP_CARDPULSE, tag=tag, email_verified=True)
    u.set_password("StrongPass123")
    u.save()
    u.set_transaction_pin("1234")
    u.save()
    w = get_or_create_wallet(u)
    w.currency = "GHS"
    w.balance = Decimal("0")
    w.save()
    return u


def _owned(owner):
    return GiftCard.objects.create(
        owner=owner, product_id=1, product_name="Amazon US", brand="Amazon",
        country="US", currency="USD", face_value=Decimal("10"),
        face_value_ngn=Decimal("16000"), cost_ngn=Decimal("16000"),
        code_encrypted="enc", pin_encrypted="enc", status=GiftCard.STATUS_OWNED,
        redeemable=True,
    )


class TradeCurrencyTests(APITestCase):
    def setUp(self):
        cache.clear()
        RateConfig.objects.all().delete()  # default 0.90 payout, 0 threshold
        self.user = _ghs_user("ghtrader@cardpulse.test", "ghtrader")

    # USD->NGN = 1600 (card market value), NGN->GHS = 0.05 (wallet payout)
    @patch("common.fx.get_rate", return_value=Decimal("0.05"))
    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_trade_pays_out_in_wallet_currency(self, _fx, _rate):
        card = _owned(self.user)
        trade = services.trade_card(self.user, card.id, "1234")
        # value 16000 NGN -> payout 14400 NGN -> 720.00 GHS to the wallet
        self.assertEqual(trade.payout_ngn, Decimal("720.00"))
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("720.00"))
        # ledger credit is in GHS
        led = LedgerEntry.objects.get(user=self.user, kind="trade_payout")
        self.assertEqual(led.currency, "GHS")
        self.assertEqual(led.amount, Decimal("720.00"))
        # profit stays NGN: 16000 - 14400 = 1600
        self.assertTrue(ProfitEntry.objects.filter(
            source="trade", amount=Decimal("1600.00"), currency="NGN").exists())


def _auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


class CatalogDisplayCurrencyTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = _ghs_user("ghcat@cardpulse.test", "ghcat")

    @patch("common.fx.get_rate", return_value=Decimal("0.05"))
    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    @patch("common.providers.get_giftcard_provider", return_value=FakeProvider())
    def test_catalog_prices_shown_in_wallet_currency(self, _prov, _rate, _fx):
        res = self.client.get(reverse("giftcards:catalog"), HTTP_AUTHORIZATION=_auth(self.user))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["price_currency"], "GHS")
        amazon = res.data["products"][0]
        self.assertEqual(amazon["price_currency"], "GHS")
        self.assertEqual(amazon["currency"], "USD")  # card face currency unchanged
        # 10 USD * 1600 = 16000 NGN; * 0.05 = 800.00 GHS (NGN price kept too)
        d0 = amazon["denominations"][0]
        self.assertEqual(d0["price_ngn"], 16000.0)
        self.assertEqual(d0["price"], 800.0)


class SaleCurrencyTests(APITestCase):
    def setUp(self):
        cache.clear()
        RateConfig.objects.all().delete()
        self.user = _ghs_user("ghseller@cardpulse.test", "ghseller")

    @patch("common.fx.get_rate", return_value=Decimal("0.05"))
    @patch.object(services, "currency_to_ngn_rate", return_value=Decimal("1600"))
    def test_sale_payout_in_wallet_currency(self, _fx, _rate):
        sale = GiftCardSale.objects.create(
            user=self.user, brand="Amazon", country="US", currency="USD",
            face_value=Decimal("100"), status=GiftCardSale.STATUS_PENDING,
        )
        admin = _ghs_user("ghadm@cardpulse.test", "ghadm")
        services.approve_sale(admin, sale.id)
        sale.refresh_from_db()
        # 100 USD * 1600 = 160000 NGN -> 90% = 144000 NGN -> 7200.00 GHS
        self.assertEqual(sale.payout_ngn, Decimal("7200.00"))
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("7200.00"))
        led = LedgerEntry.objects.get(user=self.user, kind="trade_payout")
        self.assertEqual(led.currency, "GHS")
        # profit booked in NGN: 160000 - 144000 = 16000
        self.assertTrue(ProfitEntry.objects.filter(
            source="sale", amount=Decimal("16000.00"), currency="NGN").exists())
