"""Per-currency deposits & withdrawals: the wallet moves in its own currency
while Paystack always charges/transfers in NGN (the settlement currency)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from cardpulse.models import LedgerEntry, RateConfig
from users.services import get_or_create_wallet

from . import services
from .models import Withdrawal
from .tests import cp_user, auth, FakePayout

User = get_user_model()


def ghs_user(email, tag):
    u = cp_user(email, tag)
    w = get_or_create_wallet(u)
    w.currency = "GHS"
    w.balance = Decimal("1000")  # 1000 GHS
    w.save()
    return User.objects.get(pk=u.pk)  # fresh, so .wallet isn't a stale NGN cache


class RecordingPayout(FakePayout):
    last_transfer_amount = None

    def initiate_transfer(self, recipient_code, amount, reference, reason=""):
        RecordingPayout.last_transfer_amount = Decimal(str(amount))
        return super().initiate_transfer(recipient_code, amount, reference, reason)


class WithdrawalCurrencyTests(APITestCase):
    def setUp(self):
        RateConfig.objects.all().delete()
        self.user = ghs_user("ghw@cardpulse.test", "ghw")
        RecordingPayout.last_transfer_amount = None

    # GHS->NGN = 20  => 500 GHS withdrawn = 10,000 NGN transferred
    @patch("common.fx.get_rate", return_value=Decimal("20"))
    @patch("banking.services.get_payout_provider", return_value=RecordingPayout())
    def test_withdraw_debits_ghs_transfers_ngn(self, _p, _fx):
        res = self.client.post(reverse("banking:withdraw"), {
            "amount": "500", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        self.assertEqual(res.status_code, 201, res.data)
        # wallet debited in GHS
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("500.00"))
        # Paystack got the NGN equivalent
        self.assertEqual(RecordingPayout.last_transfer_amount, Decimal("10000.00"))
        wd = Withdrawal.objects.get(user=self.user)
        self.assertEqual(wd.currency, "GHS")
        self.assertEqual(wd.amount, Decimal("500.00"))
        self.assertEqual(wd.amount_ngn, Decimal("10000.00"))
        # ledger debit is in GHS
        led = LedgerEntry.objects.get(user=self.user, kind="withdrawal")
        self.assertEqual(led.currency, "GHS")
        self.assertEqual(led.amount, Decimal("500.00"))

    @patch("common.fx.get_rate", return_value=Decimal("20"))
    @patch("banking.services.get_payout_provider")
    def test_refund_credits_back_in_ghs(self, mock_provider, _fx):
        from .tests import TransferRaises
        mock_provider.return_value = TransferRaises()
        self.client.post(reverse("banking:withdraw"), {
            "amount": "500", "bank_code": "001", "account_number": "0123456789", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.user))
        # provider failed -> full GHS refund
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("1000.00"))
        self.assertTrue(LedgerEntry.objects.filter(
            user=self.user, kind="reversal", currency="GHS", amount=Decimal("500.00")).exists())


class DepositCurrencyTests(APITestCase):
    def setUp(self):
        self.user = ghs_user("ghd@cardpulse.test", "ghd")

    @patch("common.fx.get_rate", return_value=Decimal("20"))
    @patch("banking.services.requests.post")
    def test_deposit_charges_ngn_equivalent(self, mock_post, _fx):
        mock_post.return_value.json.return_value = {
            "status": True, "data": {"authorization_url": "http://pay", "reference": "ref_1"},
        }
        out = services.create_deposit(self.user, "500")  # 500 GHS
        # Paystack charged the NGN equivalent: 500 * 20 = 10,000 NGN -> 1,000,000 kobo
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["amount"], 1000000)
        self.assertEqual(body["currency"], "NGN")
        from payments.models import Deposit
        dep = Deposit.objects.get(id=out["deposit_id"])
        self.assertEqual(dep.amount, Decimal("500.00"))   # credited in GHS on success
        self.assertEqual(dep.currency, "GHS")
        self.assertEqual(dep.provider_payload.get("charge_ngn"), "10000.00")
