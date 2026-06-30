from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from banking.models import Withdrawal
from giftcards.models import GiftCard, GiftCardTrade
from users.services import get_or_create_wallet

User = get_user_model()


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


def admin_user():
    u = User(email="staff@cp.test", username="staff@cp.test", full_name="Staff",
             app=User.APP_SOCIALPULSE, is_staff=True)
    u.set_password("x")
    u.save()
    return u


def cp_user(email, tag):
    u = User(email=email, username=email, full_name=email.split("@")[0],
             app=User.APP_CARDPULSE, tag=tag)
    u.set_password("x")
    u.save()
    get_or_create_wallet(u)
    return u


class AdminAuthTests(APITestCase):
    def test_overview_requires_admin(self):
        member = cp_user("m@cp.test", "member")
        res = self.client.get(reverse("cardpulse_admin:overview"), HTTP_AUTHORIZATION=auth(member))
        self.assertEqual(res.status_code, 403)

    def test_overview_ok_for_admin(self):
        res = self.client.get(reverse("cardpulse_admin:overview"),
                              HTTP_AUTHORIZATION=auth(admin_user()))
        self.assertEqual(res.status_code, 200)
        self.assertIn("total_profit", res.data)
        self.assertIn("trades", res.data)
        self.assertIn("withdrawals", res.data)


class AdminRatesTests(APITestCase):
    def test_get_and_update_rates(self):
        admin = admin_user()
        res = self.client.get(reverse("cardpulse_admin:rates"), HTTP_AUTHORIZATION=auth(admin))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["trade_payout_rate"], 0.9)

        res2 = self.client.put(reverse("cardpulse_admin:rates"),
                               {"trade_payout_rate": "0.85", "manual_review_threshold": "50000"},
                               format="json", HTTP_AUTHORIZATION=auth(admin))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data["trade_payout_rate"], 0.85)
        self.assertEqual(res2.data["manual_review_threshold"], 50000.0)


class AdminTradeQueueTests(APITestCase):
    def test_approve_pending_trade_pays_out(self):
        admin = admin_user()
        trader = cp_user("t@cp.test", "trader1")
        card = GiftCard.objects.create(
            owner=trader, product_id=1, product_name="Amazon", currency="USD",
            face_value=Decimal("10"), face_value_ngn=Decimal("16000"), cost_ngn=Decimal("16000"),
            code_encrypted="enc", pin_encrypted="enc", status=GiftCard.STATUS_OWNED, redeemable=False,
        )
        trade = GiftCardTrade.objects.create(
            user=trader, card=card, face_value=Decimal("10"), currency="USD",
            value_ngn=Decimal("16000"), payout_rate=Decimal("0.9000"),
            payout_ngn=Decimal("14400"), profit_ngn=Decimal("1600"),
            status=GiftCardTrade.STATUS_PENDING_REVIEW,
        )
        res = self.client.post(reverse("cardpulse_admin:trade-approve", args=[trade.id]),
                               HTTP_AUTHORIZATION=auth(admin))
        self.assertEqual(res.status_code, 200, res.data)
        trade.refresh_from_db()
        card.refresh_from_db()
        trader.wallet.refresh_from_db()
        self.assertEqual(trade.status, "completed")
        self.assertEqual(trader.wallet.balance, Decimal("14400.00"))
        self.assertIsNone(card.owner_id)


class FakePayout:
    def initiate_transfer(self, recipient_code, amount, reference, reason=""):
        return {"status": True, "data": {"transfer_code": "TRF_x", "status": "success"}}


class AdminWithdrawalQueueTests(APITestCase):
    @patch("banking.services.get_payout_provider", return_value=FakePayout())
    def test_approve_pending_withdrawal(self, _p):
        admin = admin_user()
        user = cp_user("wq@cp.test", "wq")
        wd = Withdrawal.objects.create(
            user=user, amount=Decimal("5000"), bank_code="001", account_number="0123456789",
            recipient_code="RCP_x", reference="cpw_test", idempotency_key="k1",
            status=Withdrawal.STATUS_PENDING_REVIEW,
        )
        res = self.client.post(reverse("cardpulse_admin:withdrawal-approve", args=[wd.id]),
                               HTTP_AUTHORIZATION=auth(admin))
        self.assertEqual(res.status_code, 200, res.data)
        wd.refresh_from_db()
        self.assertEqual(wd.status, Withdrawal.STATUS_SUCCESS)
