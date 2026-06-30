from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from users.models import Wallet
from .models import VirtualNumber

User = get_user_model()


def _user():
    u = User.objects.create_user(username="ac", email="ac@test.com",
                                 password="pass12345", full_name="AC")
    Wallet.objects.create(user=u, balance=Decimal("1000"),
                          reserved_balance=Decimal("20"), currency="NGN")
    return u


def _number(user, *, age_minutes, cost="10.00"):
    vn = VirtualNumber.objects.create(
        user=user, country="US", service="whatsapp",
        phone_number="+1555", activation_id=f"act-{age_minutes}",
        cost=Decimal(cost), status="Pending", charged=False,
    )
    # created_at is auto_now_add; force it back by `age_minutes`.
    VirtualNumber.objects.filter(pk=vn.pk).update(
        created_at=timezone.now() - timezone.timedelta(minutes=age_minutes)
    )
    return VirtualNumber.objects.get(pk=vn.pk)


class AutoCancelWindowTests(APITestCase):
    def test_fresh_order_is_not_cancelled(self):
        user = _user()
        fresh = _number(user, age_minutes=2)
        call_command("auto_cancel_numbers")
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, "Pending")

    def test_number_past_window_is_cancelled_and_hold_released(self):
        # Pin a 20-min window (the production default) for a deterministic test;
        # this only affects this test process, never the server configuration.
        import os
        prev = os.environ.get("VIRTUALNUMBER_AUTO_CANCEL_MINUTES")
        os.environ["VIRTUALNUMBER_AUTO_CANCEL_MINUTES"] = "20"
        try:
            user = _user()
            old = _number(user, age_minutes=21, cost="10.00")
            call_command("auto_cancel_numbers")
            old.refresh_from_db()
            self.assertEqual(old.status, "Cancelled")
            user.wallet.refresh_from_db()
            # 20 reserved - 10 released = 10
            self.assertEqual(user.wallet.reserved_balance, Decimal("10.00"))
        finally:
            if prev is None:
                os.environ.pop("VIRTUALNUMBER_AUTO_CANCEL_MINUTES", None)
            else:
                os.environ["VIRTUALNUMBER_AUTO_CANCEL_MINUTES"] = prev

    def test_floor_protects_fresh_orders_even_if_env_is_zero(self):
        # A misconfigured 0-minute window must NOT cancel a just-placed order.
        import os
        prev = os.environ.get("VIRTUALNUMBER_AUTO_CANCEL_MINUTES")
        os.environ["VIRTUALNUMBER_AUTO_CANCEL_MINUTES"] = "0"
        try:
            user = _user()
            fresh = _number(user, age_minutes=1)
            call_command("auto_cancel_numbers")
            fresh.refresh_from_db()
            self.assertEqual(fresh.status, "Pending")
        finally:
            if prev is None:
                os.environ.pop("VIRTUALNUMBER_AUTO_CANCEL_MINUTES", None)
            else:
                os.environ["VIRTUALNUMBER_AUTO_CANCEL_MINUTES"] = prev
