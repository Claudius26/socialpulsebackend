from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from users.models import Wallet
from .models import ApiKey, generate_api_key

User = get_user_model()

POOLS = {"status": "success", "data": [
    {"pool": "5", "price": "100", "name": "WhatsApp US", "success_rate": "90"},
]}
PRICE = Decimal("120.00")  # 100 * (1 + 0.20 API margin)


def make_user(balance="0.00", api="0.00"):
    user = User.objects.create_user(
        username="dev", email="dev@test.com", password="pass12345", full_name="Dev"
    )
    Wallet.objects.create(user=user, balance=Decimal(balance),
                          api_balance=Decimal(api), currency="NGN")
    return user


def make_key(user):
    full, prefix, h = generate_api_key()
    ApiKey.objects.create(user=user, name="k", prefix=prefix, key_hash=h)
    return full


class KeyManagementTests(APITestCase):
    def test_generate_view_and_regenerate(self):
        user = make_user()
        self.client.force_authenticate(user=user)

        # No key yet
        self.assertFalse(self.client.get("/api/developer/key/").data["has_key"])

        # Generate
        r = self.client.post("/api/developer/key/")
        self.assertEqual(r.status_code, 201)
        first = r.data["key"]
        self.assertTrue(first.startswith("sp_live_"))

        # GET returns the same key (so the owner can view it)
        r2 = self.client.get("/api/developer/key/")
        self.assertTrue(r2.data["has_key"])
        self.assertEqual(r2.data["key"], first)

        # Regenerate replaces it; exactly one key remains and it changed
        second = self.client.post("/api/developer/key/").data["key"]
        self.assertNotEqual(first, second)
        self.assertEqual(ApiKey.objects.filter(user=user).count(), 1)


class CreditTests(APITestCase):
    def test_topup_moves_funds_from_wallet_to_api(self):
        user = make_user(balance="1000.00")
        self.client.force_authenticate(user=user)
        r = self.client.post("/api/developer/credit/topup/", {"amount": "400"}, format="json")
        self.assertEqual(r.status_code, 200)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("600.00"))
        self.assertEqual(user.wallet.api_balance, Decimal("400.00"))

    def test_topup_rejects_over_balance(self):
        user = make_user(balance="100.00")
        self.client.force_authenticate(user=user)
        r = self.client.post("/api/developer/credit/topup/", {"amount": "400"}, format="json")
        self.assertEqual(r.status_code, 400)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.api_balance, Decimal("0.00"))

    def test_withdraw_moves_credit_back_to_wallet(self):
        user = make_user(balance="1000.00")
        self.client.force_authenticate(user=user)
        self.client.post("/api/developer/credit/topup/", {"amount": "400"}, format="json")
        r = self.client.post("/api/developer/credit/withdraw/", {"amount": "150"}, format="json")
        self.assertEqual(r.status_code, 200)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("750.00"))      # 600 + 150
        self.assertEqual(user.wallet.api_balance, Decimal("250.00"))  # 400 - 150


class ApiKeyAuthTests(APITestCase):
    def test_balance_requires_valid_key(self):
        user = make_user(api="500.00")
        self.assertIn(self.client.get("/api/v1/balance/").status_code, (401, 403))  # no key
        self.assertEqual(
            self.client.get("/api/v1/balance/", HTTP_AUTHORIZATION="Api-Key sp_live_bad").status_code,
            401,
        )
        key = make_key(user)
        r = self.client.get("/api/v1/balance/", HTTP_AUTHORIZATION=f"Api-Key {key}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["api_balance"], 500.0)


class ApiPurchaseTests(APITestCase):
    @patch("developer.services.get_otp_provider")
    def test_purchase_reserves_then_sms_charges(self, mock_prov):
        prov = MagicMock()
        prov.list_pools.return_value = POOLS
        prov.rent.return_value = {"status": "success", "data": {"order_id": "o1", "number": "+1555"}}
        prov.get_sms.return_value = {"status": "success", "data": {"sms_code": "123456"}}
        mock_prov.return_value = prov

        user = make_user(api="1000.00")
        auth = {"HTTP_AUTHORIZATION": f"Api-Key {make_key(user)}"}

        r = self.client.post("/api/v1/numbers/purchase/",
                             {"service": "whatsapp", "country": "US", "pool_id": "5"},
                             format="json", **auth)
        self.assertEqual(r.status_code, 201)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.api_reserved_balance, PRICE)   # held
        self.assertEqual(user.wallet.api_balance, Decimal("1000.00"))  # not charged yet

        r2 = self.client.get("/api/v1/numbers/o1/sms/", **auth)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["sms"], "123456")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.api_balance, Decimal("880.00"))      # charged once (1000 - 120)
        self.assertEqual(user.wallet.api_reserved_balance, Decimal("0.00"))

    @patch("developer.services.get_otp_provider")
    def test_purchase_blocks_on_insufficient_credit(self, mock_prov):
        prov = MagicMock()
        prov.list_pools.return_value = POOLS
        mock_prov.return_value = prov

        user = make_user(api="50.00")
        r = self.client.post("/api/v1/numbers/purchase/",
                             {"service": "whatsapp", "country": "US", "pool_id": "5"},
                             format="json", HTTP_AUTHORIZATION=f"Api-Key {make_key(user)}")
        self.assertEqual(r.status_code, 402)
        prov.rent.assert_not_called()
