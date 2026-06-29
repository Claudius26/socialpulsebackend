from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from users.models import Wallet
from .models import VirtualNumber

User = get_user_model()

# price 100 * (1 + 0.40 in-app margin) = 140.00
POOLS = {"status": "success", "data": [
    {"pool": "5", "price": "100", "name": "WhatsApp US", "success_rate": "90"},
]}
FINAL_PRICE = Decimal("140.00")


def make_user(balance="1000.00", reserved="0.00"):
    user = User.objects.create_user(
        username="vn", email="vn@test.com", password="pass12345", full_name="VN User"
    )
    Wallet.objects.create(user=user, balance=Decimal(balance),
                          reserved_balance=Decimal(reserved), currency="NGN")
    return user


class VirtualNumberMoneyTests(APITestCase):
    @patch("virtualnumbers.views.get_otp_provider")
    def test_purchase_reserves_funds(self, mock_provider):
        prov = MagicMock()
        prov.list_pools.return_value = POOLS
        prov.rent.return_value = {"status": "success", "data": {"order_id": "o1", "number": "+1555"}}
        mock_provider.return_value = prov

        user = make_user(balance="1000.00")
        self.client.force_authenticate(user=user)
        resp = self.client.post("/api/virtualnumbers/purchase/",
                                {"service": "whatsapp", "country": "US", "pool_id": "5"}, format="json")
        self.assertEqual(resp.status_code, 201)

        user.wallet.refresh_from_db()
        # Money is HELD, not yet spent: reserved goes up, balance stays.
        self.assertEqual(user.wallet.reserved_balance, FINAL_PRICE)
        self.assertEqual(user.wallet.balance, Decimal("1000.00"))
        vn = VirtualNumber.objects.get(user=user)
        self.assertEqual(vn.cost, FINAL_PRICE)
        self.assertEqual(vn.status, "Pending")
        self.assertFalse(vn.charged)

    @patch("virtualnumbers.views.get_otp_provider")
    def test_get_sms_charges_once(self, mock_provider):
        prov = MagicMock()
        prov.get_sms.return_value = {"status": "success", "data": {"sms_code": "123456"}}
        mock_provider.return_value = prov

        user = make_user(balance="1000.00", reserved="140.00")
        vn = VirtualNumber.objects.create(user=user, country="US", service="whatsapp",
                                          phone_number="+1555", activation_id="o1",
                                          cost=FINAL_PRICE, status="Pending", charged=False)
        self.client.force_authenticate(user=user)
        resp = self.client.get(f"/api/virtualnumbers/sms/{vn.activation_id}/")
        self.assertEqual(resp.status_code, 200)

        user.wallet.refresh_from_db()
        # Hold is consumed: balance and reserved both drop by the cost.
        self.assertEqual(user.wallet.balance, Decimal("860.00"))
        self.assertEqual(user.wallet.reserved_balance, Decimal("0.00"))
        vn.refresh_from_db()
        self.assertTrue(vn.charged)
        self.assertEqual(vn.status, "Active")

    @patch("virtualnumbers.views.get_otp_provider")
    def test_cancel_releases_reservation(self, mock_provider):
        prov = MagicMock()
        prov.cancel.return_value = {"status": "success"}
        mock_provider.return_value = prov

        user = make_user(balance="1000.00", reserved="140.00")
        vn = VirtualNumber.objects.create(user=user, country="US", service="whatsapp",
                                          phone_number="+1555", activation_id="o1",
                                          cost=FINAL_PRICE, status="Pending", charged=False)
        # Make it older than the 5-minute minimum (created_at is auto_now_add).
        VirtualNumber.objects.filter(pk=vn.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=10)
        )
        self.client.force_authenticate(user=user)
        resp = self.client.post("/api/virtualnumbers/cancel/", {"activation_id": "o1"}, format="json")
        self.assertEqual(resp.status_code, 200)

        user.wallet.refresh_from_db()
        # Reservation released; available balance restored.
        self.assertEqual(user.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(user.wallet.balance, Decimal("1000.00"))
        vn.refresh_from_db()
        self.assertEqual(vn.status, "Cancelled")
