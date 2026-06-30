from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import RateConfig, AuditLog

User = get_user_model()


def make_user(email, app=User.APP_CARDPULSE, tag=None, password="StrongPass123"):
    u = User(email=email, username=email, full_name="Test User", app=app, tag=tag)
    u.set_password(password)
    u.save()
    return u


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


class RegistrationTests(APITestCase):
    def test_register_creates_cardpulse_user_with_wallet_and_token(self):
        res = self.client.post(reverse("cardpulse:register"), {
            "full_name": "Ada Lovelace",
            "email": "ada@cardpulse.test",
            "password": "StrongPass123",
            "password2": "StrongPass123",
            "tag": "ada",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn("token", res.data)
        self.assertEqual(res.data["user"]["tag"], "ada")
        self.assertEqual(res.data["user"]["wallet"]["balance"], 0.0)
        user = User.objects.get(email="ada@cardpulse.test")
        self.assertEqual(user.app, User.APP_CARDPULSE)
        self.assertTrue(hasattr(user, "wallet"))

    def test_register_auto_generates_tag_when_missing(self):
        res = self.client.post(reverse("cardpulse:register"), {
            "full_name": "No Tag",
            "email": "notag@cardpulse.test",
            "password": "StrongPass123",
            "password2": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["user"]["tag"])

    def test_register_rejects_taken_tag(self):
        make_user("first@cardpulse.test", tag="taken")
        res = self.client.post(reverse("cardpulse:register"), {
            "full_name": "Second",
            "email": "second@cardpulse.test",
            "password": "StrongPass123",
            "password2": "StrongPass123",
            "tag": "taken",
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("tag", res.data)


class LoginRealmIsolationTests(APITestCase):
    def test_cardpulse_user_can_login(self):
        make_user("cp@cardpulse.test", app=User.APP_CARDPULSE, tag="cp")
        res = self.client.post(reverse("cardpulse:login"), {
            "email": "cp@cardpulse.test", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("token", res.data)

    def test_socialpulse_user_cannot_login_via_cardpulse(self):
        make_user("web@socialpulse.test", app=User.APP_SOCIALPULSE)
        res = self.client.post(reverse("cardpulse:login"), {
            "email": "web@socialpulse.test", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_socialpulse_token_cannot_access_cardpulse_me(self):
        web = make_user("web2@socialpulse.test", app=User.APP_SOCIALPULSE)
        res = self.client.get(reverse("cardpulse:me"), HTTP_AUTHORIZATION=auth(web))
        self.assertEqual(res.status_code, 403)

    def test_cardpulse_token_accesses_me(self):
        cp = make_user("cp2@cardpulse.test", tag="cp2")
        res = self.client.get(reverse("cardpulse:me"), HTTP_AUTHORIZATION=auth(cp))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["user"]["email"], "cp2@cardpulse.test")


class TagTests(APITestCase):
    def test_tag_check_available(self):
        res = self.client.get(reverse("cardpulse:tag-check"), {"tag": "freshtag"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["available"])

    def test_tag_check_taken_returns_suggestions(self):
        make_user("u@cardpulse.test", tag="busy")
        res = self.client.get(reverse("cardpulse:tag-check"), {"tag": "busy"})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["available"])
        self.assertIn("suggestions", res.data)

    def test_invalid_tag_rejected(self):
        res = self.client.get(reverse("cardpulse:tag-check"), {"tag": "ab"})
        self.assertFalse(res.data["available"])


class TransactionPinTests(APITestCase):
    def test_set_pin_requires_correct_password(self):
        cp = make_user("pin@cardpulse.test", tag="pinu")
        res = self.client.post(reverse("cardpulse:set-pin"), {
            "password": "WrongPass", "pin": "1234", "confirm_pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(cp))
        self.assertEqual(res.status_code, 400)

    def test_set_and_verify_pin(self):
        cp = make_user("pin2@cardpulse.test", tag="pinu2")
        res = self.client.post(reverse("cardpulse:set-pin"), {
            "password": "StrongPass123", "pin": "1234", "confirm_pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(cp))
        self.assertEqual(res.status_code, 200, res.data)
        cp.refresh_from_db()
        self.assertTrue(cp.has_transaction_pin)
        self.assertTrue(cp.check_transaction_pin("1234"))
        self.assertFalse(cp.check_transaction_pin("0000"))

    def test_pin_not_stored_in_plaintext(self):
        cp = make_user("pin3@cardpulse.test", tag="pinu3")
        cp.set_transaction_pin("4321")
        cp.save()
        cp.refresh_from_db()
        self.assertNotEqual(cp.transaction_pin, "4321")


class RateConfigTests(APITestCase):
    def test_default_payout_rate_is_90_percent(self):
        cfg = RateConfig.get_solo()
        self.assertEqual(cfg.trade_payout_rate, Decimal("0.9000"))

    def test_get_solo_is_idempotent(self):
        a = RateConfig.get_solo()
        b = RateConfig.get_solo()
        self.assertEqual(a.pk, b.pk)


class AuditTests(APITestCase):
    def test_register_writes_audit_log(self):
        self.client.post(reverse("cardpulse:register"), {
            "full_name": "Audit", "email": "audit@cardpulse.test",
            "password": "StrongPass123", "password2": "StrongPass123", "tag": "audituser",
        }, format="json")
        self.assertTrue(AuditLog.objects.filter(action="cardpulse_register").exists())
