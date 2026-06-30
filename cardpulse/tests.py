from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import RateConfig, AuditLog, EmailOTP

User = get_user_model()


def make_user(email, app=User.APP_CARDPULSE, tag=None, password="StrongPass123", verified=True):
    u = User(email=email, username=email, full_name="Test User", app=app, tag=tag,
             email_verified=verified)
    u.set_password(password)
    u.save()
    return u


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


class RegistrationTests(APITestCase):
    def _payload(self, **over):
        body = {
            "first_name": "Ada", "last_name": "Lovelace",
            "email": "ada@cardpulse.test",
            "password": "StrongPass123", "password2": "StrongPass123",
        }
        body.update(over)
        return body

    def test_register_creates_user_with_auto_username_wallet_token(self):
        res = self.client.post(reverse("cardpulse:register"), self._payload(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn("token", res.data)
        self.assertTrue(res.data["user"]["tag"])             # auto-generated username
        self.assertEqual(res.data["user"]["username"], res.data["user"]["tag"])
        self.assertFalse(res.data["user"]["email_verified"])  # must verify email
        user = User.objects.get(email="ada@cardpulse.test")
        self.assertEqual(user.full_name, "Ada Lovelace")
        self.assertEqual(user.app, User.APP_CARDPULSE)
        self.assertTrue(hasattr(user, "wallet"))

    def test_register_sends_otp_email(self):
        self.client.post(reverse("cardpulse:register"), self._payload(), format="json")
        user = User.objects.get(email="ada@cardpulse.test")
        self.assertTrue(EmailOTP.objects.filter(user=user, purpose="verify", used=False).exists())

    def test_register_rejects_duplicate_email(self):
        make_user("dupe@cardpulse.test", tag="dupe")
        res = self.client.post(reverse("cardpulse:register"),
                               self._payload(email="dupe@cardpulse.test"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data)

    def test_passwords_must_match(self):
        res = self.client.post(reverse("cardpulse:register"),
                               self._payload(password2="different"), format="json")
        self.assertEqual(res.status_code, 400)


class LoginRealmIsolationTests(APITestCase):
    def test_login_with_email(self):
        make_user("cp@cardpulse.test", app=User.APP_CARDPULSE, tag="cp")
        res = self.client.post(reverse("cardpulse:login"), {
            "login": "cp@cardpulse.test", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("token", res.data)

    def test_login_with_username(self):
        make_user("cpu@cardpulse.test", app=User.APP_CARDPULSE, tag="ada_l")
        res = self.client.post(reverse("cardpulse:login"), {
            "login": "ada_l", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("token", res.data)

    def test_socialpulse_user_cannot_login_via_cardpulse(self):
        make_user("web@socialpulse.test", app=User.APP_SOCIALPULSE)
        res = self.client.post(reverse("cardpulse:login"), {
            "login": "web@socialpulse.test", "password": "StrongPass123",
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
            "first_name": "Aud", "last_name": "It", "email": "audit@cardpulse.test",
            "password": "StrongPass123", "password2": "StrongPass123",
        }, format="json")
        self.assertTrue(AuditLog.objects.filter(action="cardpulse_register").exists())


class EmailVerificationTests(APITestCase):
    def _register(self, email="v@cardpulse.test"):
        self.client.post(reverse("cardpulse:register"), {
            "first_name": "Ver", "last_name": "Ify", "email": email,
            "password": "StrongPass123", "password2": "StrongPass123",
        }, format="json")
        return User.objects.get(email=email)

    def test_verify_with_correct_code(self):
        user = self._register()
        otp = EmailOTP.objects.filter(user=user, used=False).first()
        # We only stored the hash; reissue a known code to test the verify path.
        from cardpulse import email_utils
        code = email_utils.issue_otp(user)
        res = self.client.post(reverse("cardpulse:verify-email"), {"code": code},
                               format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_verify_with_wrong_code_fails(self):
        user = self._register("w2@cardpulse.test")
        res = self.client.post(reverse("cardpulse:verify-email"), {"code": "000000"},
                               format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_unverified_user_blocked_from_money_action(self):
        user = self._register("blocked@cardpulse.test")
        # buying requires a verified email
        res = self.client.post(reverse("giftcards:buy"), {"product_id": 1, "face_value": "10"},
                               format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 403)

    def test_change_password(self):
        user = make_user("pw@cardpulse.test", tag="pwuser")
        res = self.client.post(reverse("cardpulse:change-password"), {
            "old_password": "StrongPass123", "new_password": "NewStrongPass456",
        }, format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass456"))


class SetPhoneTests(APITestCase):
    def test_user_can_update_phone(self):
        user = make_user("ph@cardpulse.test", tag="phuser")
        res = self.client.post(reverse("cardpulse:set-phone"), {"phone": "+233 20 123 4567"},
                               format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertEqual(user.phone, "+233 20 123 4567")

    def test_invalid_phone_rejected(self):
        user = make_user("ph2@cardpulse.test", tag="phuser2")
        res = self.client.post(reverse("cardpulse:set-phone"), {"phone": "abc"},
                               format="json", HTTP_AUTHORIZATION=auth(user))
        self.assertEqual(res.status_code, 400)

    def test_phone_requires_auth(self):
        res = self.client.post(reverse("cardpulse:set-phone"), {"phone": "+2348012345678"},
                               format="json")
        self.assertIn(res.status_code, (401, 403))


class AdminBlockedFromAppTests(APITestCase):
    """Admin/staff accounts are web-dashboard only — never allowed in the app."""

    def test_admin_cannot_login_to_app(self):
        admin = make_user("boss@cardpulse.test", app=User.APP_CARDPULSE, tag="boss")
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        res = self.client.post(reverse("cardpulse:login"), {
            "login": "boss@cardpulse.test", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertIn("web dashboard", res.data["error"])

    def test_admin_token_cannot_access_app_endpoints(self):
        admin = make_user("boss2@cardpulse.test", app=User.APP_CARDPULSE, tag="boss2")
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        res = self.client.get(reverse("cardpulse:me"), HTTP_AUTHORIZATION=auth(admin))
        self.assertEqual(res.status_code, 403)

    def test_normal_user_still_logs_in(self):
        make_user("normal@cardpulse.test", app=User.APP_CARDPULSE, tag="normaluser")
        res = self.client.post(reverse("cardpulse:login"), {
            "login": "normal@cardpulse.test", "password": "StrongPass123",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
