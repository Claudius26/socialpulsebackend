from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from users.services import get_or_create_wallet

User = get_user_model()


def auth(user):
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = user.app
    return f"Bearer {refresh.access_token}"


class CardPulseNumbersTests(APITestCase):
    def test_cardpulse_user_reaches_number_history(self):
        u = User(email="num@cardpulse.test", username="num@cardpulse.test",
                 full_name="Num", app=User.APP_CARDPULSE, tag="numuser")
        u.set_password("x")
        u.save()
        get_or_create_wallet(u)
        res = self.client.get(reverse("cardpulse_numbers:number_history"),
                              HTTP_AUTHORIZATION=auth(u))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])

    def test_number_history_requires_auth(self):
        res = self.client.get(reverse("cardpulse_numbers:number_history"))
        self.assertIn(res.status_code, (401, 403))
