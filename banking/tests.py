from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from cardpulse.models import LedgerEntry, RateConfig
from common.providers import ProviderError
from users.services import get_or_create_wallet

from . import services
from .models import Withdrawal

User = get_user_model()


def cp_user(email="w@cardpulse.test", tag="wuser", pin="1234"):
    u = User(email=email, username=email, full_name="W User", app=User.APP_CARDPULSE, tag=tag)
    u.set_password("StrongPass123")
    if pin:
        u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    return u


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


def fund(user, amount):
    w = get_or_create_wallet(user)
    w.balance = Decimal(str(amount))
    w.save(update_fields=["balance"])
    return w


class FakePayout:
    transfer_status = "success"

    def resolve_account(self, account_number, bank_code):
        return {"status": True, "data": {"account_name": "John Doe"}}

    def create_recipient(self, name, account_number, bank_code):
        return {"status": True, "data": {"recipient_code": "RCP_test"}}

    def initiate_transfer(self, recipient_code, amount, reference, reason=""):
        return {"status": True, "data": {"transfer_code": "TRF_test", "status": self.transfer_status}}

    def list_banks(self):
        return [{"name": "Test Bank", "code": "001"}]


class PendingPayout(FakePayout):
    transfer_status = "pending"


class TransferRaises(FakePayout):
    def initiate_transfer(self, *a, **k):
        raise ProviderError("paystack down")


class WithdrawalTests(APITestCase):
    def setUp(self):
        RateConfig.objects.all().delete()  # threshold 0 -> auto
        self.user = cp_user()
        fund(self.user, 20000)

    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_successful_withdrawal_debits_wallet(self, _p):
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "success")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("15000.00"))
        self.assertTrue(LedgerEntry.objects.filter(user=self.user, kind="withdrawal").exists())

    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_insufficient_funds(self, _p):
        fund(self.user, 0)
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 402)

    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_wrong_pin(self, _p):
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "0000",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 403)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("20000.00"))

    @patch("banking.services.get_payout_provider", return_value=TransferRaises())
    def test_transfer_failure_refunds(self, _p):
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.data["status"], "failed")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("20000.00"))  # refunded

    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_idempotency_no_double_withdraw(self, _p):
        payload = {"amount": "5000", "bank_code": "001", "account_number": "0123456789",
                   "pin": "1234", "idempotency_key": "wd-key"}
        r1 = self.client.post(reverse("banking:withdraw"), payload, format="json",
                              HTTP_AUTHORIZATION=auth(self.user))
        r2 = self.client.post(reverse("banking:withdraw"), payload, format="json",
                              HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(r1.data["id"], r2.data["id"])
        self.assertEqual(Withdrawal.objects.filter(idempotency_key="wd-key").count(), 1)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("15000.00"))

    @patch("banking.services.get_payout_provider", return_value=PendingPayout())
    def test_webhook_transfer_failed_refunds(self, _p):
        self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        wd = Withdrawal.objects.get(user=self.user)
        self.assertEqual(wd.status, Withdrawal.STATUS_PROCESSING)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("15000.00"))

        services.handle_transfer_event("transfer.failed", {"transfer_code": "TRF_test"})
        wd.refresh_from_db()
        self.assertEqual(wd.status, Withdrawal.STATUS_FAILED)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("20000.00"))  # refunded

    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_above_threshold_review_then_approve(self, _p):
        cfg = RateConfig.get_solo()
        cfg.manual_review_threshold = Decimal("1000")  # 5000 >= 1000
        cfg.save()
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "5000", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.data["status"], "pending_review")
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("15000.00"))  # held

        admin = cp_user("admin@cardpulse.test", "adminw")
        wd = Withdrawal.objects.get(user=self.user)
        services.approve_withdrawal(admin, wd.id)
        wd.refresh_from_db()
        self.assertEqual(wd.status, Withdrawal.STATUS_SUCCESS)


class BankListTests(APITestCase):
    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_list_banks(self, _p):
        user = cp_user("b@cardpulse.test", "banklister")
        res = self.client.get(reverse("banking:banks"), HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["banks"][0]["code"], "001")
