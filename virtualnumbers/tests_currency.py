from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from users.services import get_or_create_wallet

User = get_user_model()


class FakeOtp:
    def list_pools(self, country, service):
        return {"status": "success", "data": [
            {"pool": "1", "name": "WhatsApp", "success_rate": 90, "price": 100}
        ]}

    def rent(self, country, service, pool_id, provider=None):
        return {"status": "success", "data": {"order_id": "ord-1", "number": "+1555000111"}}


def auth(user):
    return f"Bearer {RefreshToken.for_user(user).access_token}"


class NumberCurrencyTests(APITestCase):
    def setUp(self):
        self.user = User(email="gh@socialpulse.test", username="gh@socialpulse.test", full_name="GH")
        self.user.set_password("x")
        self.user.save()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.currency = "GHS"
        self.wallet.balance = Decimal("1000")  # 1000 GHS
        self.wallet.save()

    @patch("virtualnumbers.views.get_rate", return_value=Decimal("0.05"))
    @patch("virtualnumbers.views.get_otp_provider", return_value=FakeOtp())
    def test_services_show_user_currency(self, _otp, _rate):
        res = self.client.get(reverse("get_services"), {"service": "whatsapp", "country": "US"},
                              HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 200, res.data)
        svc = res.data["services"][0]
        self.assertEqual(svc["currency"], "GHS")
        # 100 base * 1.40 margin = 140 NGN; * 0.05 = 7.00 GHS
        self.assertEqual(svc["price"], 7.0)

    @patch("common.fx.get_rate", return_value=Decimal("0.05"))
    @patch("virtualnumbers.views.get_otp_provider", return_value=FakeOtp())
    def test_purchase_charges_in_wallet_currency(self, _otp, _rate):
        res = self.client.post(reverse("purchase_number"),
                               {"service": "whatsapp", "country": "US", "pool_id": "1"},
                               format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        self.wallet.refresh_from_db()
        # 140 NGN -> 7 GHS reserved (not 140)
        self.assertEqual(self.wallet.reserved_balance, Decimal("7.00"))
        from virtualnumbers.models import VirtualNumber
        vn = VirtualNumber.objects.get(user=self.user)
        self.assertEqual(vn.cost, Decimal("7.00"))
