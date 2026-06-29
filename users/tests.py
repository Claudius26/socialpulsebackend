from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from .models import Wallet
from . import services

User = get_user_model()


def make_user(email="u1@test.com", username="u1", balance="0.00"):
    user = User.objects.create_user(
        username=username, email=email, password="pass12345", full_name="Test User"
    )
    Wallet.objects.create(user=user, balance=Decimal(balance), currency="NGN")
    return user


class WalletServiceTests(TestCase):
    def test_credit_adds_funds(self):
        user = make_user(balance="100.00")
        services.credit(user, "50.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("150.00"))

    def test_debit_removes_funds(self):
        user = make_user(balance="100.00")
        services.debit(user, "30.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("70.00"))

    def test_debit_rejects_overdraw(self):
        user = make_user(balance="20.00")
        with self.assertRaises(services.InsufficientFunds):
            services.debit(user, "50.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("20.00"))  # unchanged

    def test_reserve_then_release_nets_to_zero(self):
        user = make_user(balance="100.00")
        services.reserve(user, "40.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("60.00"))
        self.assertEqual(user.wallet.reserved_balance, Decimal("40.00"))

        services.release(user, "40.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("100.00"))
        self.assertEqual(user.wallet.reserved_balance, Decimal("0.00"))

    def test_settle_reserved_consumes_hold(self):
        user = make_user(balance="100.00")
        services.reserve(user, "40.00")
        services.settle_reserved(user, "40.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("60.00"))
        self.assertEqual(user.wallet.reserved_balance, Decimal("0.00"))

    def test_release_never_creates_money(self):
        # Nothing reserved; releasing must be a safe no-op, not invent funds.
        user = make_user(balance="0.00")
        services.release(user, "999.00")
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("0.00"))
        self.assertEqual(user.wallet.reserved_balance, Decimal("0.00"))


class UserSummaryTests(TestCase):
    def test_summary_totals_and_counts(self):
        from .views import build_user_summary
        from payments.models import Deposit
        from virtualnumbers.models import VirtualNumber
        from boost.models import BoostRequest

        user = make_user(balance="0.00")
        # Deposits: only "paid" counts.
        Deposit.objects.create(user=user, amount=Decimal("1000"), status="paid", provider_reference="r1")
        Deposit.objects.create(user=user, amount=Decimal("500"), status="pending", provider_reference="r2")
        # Numbers: only charged ones count toward spend.
        VirtualNumber.objects.create(user=user, country="US", service="wa", phone_number="1",
                                     activation_id="a1", cost=Decimal("200"), charged=True)
        VirtualNumber.objects.create(user=user, country="US", service="wa", phone_number="2",
                                     activation_id="a2", cost=Decimal("300"), charged=False)
        # Boosts: Failed ones are excluded from spend.
        BoostRequest.objects.create(user=user, platform="Instagram", service="followers", target="x",
                                    quantity=10, audience="w", amount=Decimal("400"), status="Processing")
        BoostRequest.objects.create(user=user, platform="Instagram", service="followers", target="y",
                                    quantity=10, audience="w", amount=Decimal("999"), status="Failed")

        s = build_user_summary(user)
        self.assertEqual(s["totals"]["deposited"], 1000.0)
        self.assertEqual(s["totals"]["spent_on_numbers"], 200.0)
        self.assertEqual(s["totals"]["spent_on_boost"], 400.0)
        self.assertEqual(s["totals"]["overall_spending"], 600.0)
        self.assertEqual(s["counts"]["deposits_paid"], 1)
        self.assertEqual(s["counts"]["numbers_purchased"], 2)
        self.assertEqual(s["counts"]["boost_requests"], 2)


class AuthTests(TestCase):
    def test_login_rejects_bad_credentials(self):
        make_user(email="real@test.com", username="real")
        resp = self.client.post(
            "/api/login/",
            data={"email": "real@test.com", "password": "wrongpass"},
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (400, 401))

    def test_me_requires_authentication(self):
        resp = self.client.get("/api/me/")
        self.assertIn(resp.status_code, (401, 403))
