import hmac
import hashlib
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from users.models import Wallet
from payments.models import Deposit
from payments import utils

User = get_user_model()
WEBHOOK_URL = "/api/deposit/webhook/paystack/"


def make_user(balance="0.00"):
    user = User.objects.create_user(
        username="dep", email="dep@test.com", password="pass12345", full_name="Depositor"
    )
    Wallet.objects.create(user=user, balance=Decimal(balance), currency="NGN")
    return user


def sign(raw: bytes) -> str:
    return hmac.new(utils.PAYSTACK_SECRET_KEY.encode("utf-8"), raw, hashlib.sha512).hexdigest()


class PaystackWebhookTests(APITestCase):
    def _body(self, reference):
        return json.dumps({"event": "charge.success", "data": {"reference": reference}}).encode()

    def test_webhook_rejects_missing_signature(self):
        user = make_user()
        dep = Deposit.objects.create(user=user, amount=Decimal("1000.00"), provider_reference="ref-1")
        raw = self._body("ref-1")
        resp = self.client.post(WEBHOOK_URL, data=raw, content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        dep.refresh_from_db()
        self.assertEqual(dep.status, "pending")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("0.00"))  # not credited

    def test_webhook_rejects_forged_signature(self):
        user = make_user()
        Deposit.objects.create(user=user, amount=Decimal("1000.00"), provider_reference="ref-2")
        raw = self._body("ref-2")
        resp = self.client.post(
            WEBHOOK_URL, data=raw, content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="deadbeef",
        )
        self.assertEqual(resp.status_code, 401)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("0.00"))

    def test_valid_signature_credits_once_and_is_idempotent(self):
        if not utils.PAYSTACK_SECRET_KEY:
            self.skipTest("PAYSTACK_SECRET_KEY not configured in this environment")
        user = make_user()
        dep = Deposit.objects.create(user=user, amount=Decimal("1500.00"), provider_reference="ref-3")
        raw = self._body("ref-3")
        sig = sign(raw)

        # First delivery credits.
        r1 = self.client.post(
            WEBHOOK_URL, data=raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig
        )
        self.assertEqual(r1.status_code, 200)
        dep.refresh_from_db()
        self.assertEqual(dep.status, "paid")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("1500.00"))

        # Duplicate delivery (Paystack retries) must NOT credit again.
        r2 = self.client.post(
            WEBHOOK_URL, data=raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig
        )
        self.assertEqual(r2.status_code, 200)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("1500.00"))  # still once


class AdminEndpointTests(APITestCase):
    def test_admin_endpoints_require_staff(self):
        normal = User.objects.create_user(
            username="n", email="n@test.com", password="pass12345", full_name="N"
        )
        self.client.force_authenticate(user=normal)
        self.assertIn(self.client.get("/api/deposit/admin/overview/").status_code, (401, 403))
        self.assertIn(self.client.get("/api/deposit/admin/numbers/").status_code, (401, 403))

    def test_admin_overview_and_numbers(self):
        admin = User.objects.create_user(
            username="admin", email="admin@test.com", password="pass12345",
            full_name="Admin", is_staff=True,
        )
        self.client.force_authenticate(user=admin)

        r = self.client.get("/api/deposit/admin/overview/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("numbers", r.data)
        self.assertIn("deposits", r.data)

        r2 = self.client.get("/api/deposit/admin/numbers/")
        self.assertEqual(r2.status_code, 200)
        self.assertIsInstance(r2.data, list)
