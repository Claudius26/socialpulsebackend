from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from users.models import Wallet
from .models import BoostRequest

User = get_user_model()

# One matching SMM service. rate 1.0 * qty 1000 / 1000 = 1.0 USD,
# * 1.8 margin = 1.8 USD, * 1000 NGN rate = 1800.00 NGN charge.
FAKE_SERVICES = [
    {"name": "instagram followers", "service": "101", "rate": "1.0",
     "average_time": "1 hour", "min": "10", "max": "100000"},
]
EXPECTED_CHARGE = Decimal("1800.00")


def make_user(balance="5000.00"):
    user = User.objects.create_user(
        username="booster", email="booster@test.com", password="pass12345", full_name="Booster"
    )
    Wallet.objects.create(user=user, balance=Decimal(balance), currency="NGN")
    return user


def boost_payload():
    return {
        "platform": "Instagram",
        "service": "followers",
        "target": "https://instagram.com/someone",
        "quantity": 1000,
        "audience": "Worldwide",
    }


class BoostChargeTests(APITestCase):
    @patch("boost.views.get_smm_provider")
    @patch("boost.views.get_live_usd_to_ngn_rate", return_value=Decimal("1000"))
    @patch("boost.views.fetch_all_smm_services", return_value=FAKE_SERVICES)
    def test_successful_order_charges_wallet_exactly_once(self, _svc, _rate, mock_provider):
        mock_provider.return_value.place_order.return_value = {"order": 999}
        user = make_user(balance="5000.00")
        self.client.force_authenticate(user=user)

        resp = self.client.post("/api/boost/", boost_payload(), format="json")
        self.assertEqual(resp.status_code, 201)

        user.wallet.refresh_from_db()
        # 5000 - 1800 = 3200.  (A double charge would wrongly give 1400.)
        self.assertEqual(user.wallet.balance, Decimal("5000.00") - EXPECTED_CHARGE)

        boost = BoostRequest.objects.get(user=user)
        self.assertEqual(boost.status, "Processing")
        self.assertEqual(boost.smm_order_id, "999")

    @patch("boost.views.get_smm_provider")
    @patch("boost.views.get_live_usd_to_ngn_rate", return_value=Decimal("1000"))
    @patch("boost.views.fetch_all_smm_services", return_value=FAKE_SERVICES)
    def test_provider_failure_refunds_the_user(self, _svc, _rate, mock_provider):
        mock_provider.return_value.place_order.return_value = {"error": "rejected"}
        user = make_user(balance="5000.00")
        self.client.force_authenticate(user=user)

        resp = self.client.post("/api/boost/", boost_payload(), format="json")
        self.assertEqual(resp.status_code, 201)

        user.wallet.refresh_from_db()
        # Debited then refunded -> back to the original balance.
        self.assertEqual(user.wallet.balance, Decimal("5000.00"))
        self.assertEqual(BoostRequest.objects.get(user=user).status, "Failed")

    @patch("boost.views.get_smm_provider")
    @patch("boost.views.get_live_usd_to_ngn_rate", return_value=Decimal("1000"))
    @patch("boost.views.fetch_all_smm_services", return_value=FAKE_SERVICES)
    def test_insufficient_balance_blocks_order(self, _svc, _rate, mock_provider):
        user = make_user(balance="100.00")  # less than 1800 charge
        self.client.force_authenticate(user=user)

        resp = self.client.post("/api/boost/", boost_payload(), format="json")
        self.assertEqual(resp.status_code, 201)

        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("100.00"))  # untouched
        self.assertEqual(BoostRequest.objects.get(user=user).status, "Failed")
        mock_provider.return_value.place_order.assert_not_called()  # never hit the provider
