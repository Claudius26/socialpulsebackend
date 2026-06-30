from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from cardpulse.models import LedgerEntry
from giftcards.models import GiftCard
from users.services import get_or_create_wallet

from .models import Transfer

User = get_user_model()


def cp_user(email, tag, pin="1234"):
    u = User(email=email, username=email, full_name=email.split("@")[0],
             app=User.APP_CARDPULSE, tag=tag, email_verified=True)
    u.set_password("StrongPass123")
    if pin:
        u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)  # every CardPulse user has a wallet at registration
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


def make_card(owner, status=GiftCard.STATUS_OWNED, redeemable=True):
    return GiftCard.objects.create(
        owner=owner, product_id=1, product_name="Amazon US", brand="Amazon",
        country="US", currency="USD", face_value=Decimal("10"),
        face_value_ngn=Decimal("16000"), cost_ngn=Decimal("16000"),
        code_encrypted="enc", pin_encrypted="enc", status=status, redeemable=redeemable,
    )


class LookupTests(APITestCase):
    def test_lookup_found(self):
        cp_user("a@cardpulse.test", "alice")
        me = cp_user("b@cardpulse.test", "bob")
        res = self.client.get(reverse("p2p:lookup"), {"tag": "alice"},
                              HTTP_AUTHORIZATION=auth(me))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["found"])
        self.assertEqual(res.data["tag"], "alice")
        self.assertNotIn("email", res.data)

    def test_lookup_not_found(self):
        me = cp_user("b@cardpulse.test", "bob")
        res = self.client.get(reverse("p2p:lookup"), {"tag": "ghost"},
                              HTTP_AUTHORIZATION=auth(me))
        self.assertFalse(res.data["found"])


class SendCashTests(APITestCase):
    def setUp(self):
        self.sender = cp_user("sender@cardpulse.test", "sender")
        self.recipient = cp_user("recipient@cardpulse.test", "recipient")
        fund(self.sender, 10000)
        fund(self.recipient, 0)

    def test_send_cash_moves_balance_and_ledgers_both(self):
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "recipient", "amount": "2500", "pin": "1234", "note": "lunch",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 201, res.data)
        self.sender.wallet.refresh_from_db()
        self.recipient.wallet.refresh_from_db()
        self.assertEqual(self.sender.wallet.balance, Decimal("7500.00"))
        self.assertEqual(self.recipient.wallet.balance, Decimal("2500.00"))
        self.assertTrue(LedgerEntry.objects.filter(user=self.sender, kind="transfer_out").exists())
        self.assertTrue(LedgerEntry.objects.filter(user=self.recipient, kind="transfer_in").exists())

    def test_send_cash_insufficient(self):
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "recipient", "amount": "99999", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 402)
        self.sender.wallet.refresh_from_db()
        self.assertEqual(self.sender.wallet.balance, Decimal("10000.00"))

    def test_send_cash_wrong_pin(self):
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "recipient", "amount": "100", "pin": "0000",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 403)

    def test_cannot_send_to_self(self):
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "sender", "amount": "100", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 400)

    def test_requires_pin_set(self):
        nopin = cp_user("nopin@cardpulse.test", "nopin", pin=None)
        fund(nopin, 5000)
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "recipient", "amount": "100", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(nopin))
        self.assertEqual(res.status_code, 400)


class SendGiftcardTests(APITestCase):
    def setUp(self):
        self.sender = cp_user("gs@cardpulse.test", "giftsender")
        self.recipient = cp_user("gr@cardpulse.test", "giftrecipient")

    def test_send_giftcard_transfers_ownership(self):
        card = make_card(self.sender)
        res = self.client.post(reverse("p2p:send-giftcard"), {
            "tag": "giftrecipient", "card_id": card.id, "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 201, res.data)
        card.refresh_from_db()
        self.assertEqual(card.owner_id, self.recipient.id)
        # recipient now sees it in their cards; sender no longer does
        self.assertTrue(GiftCard.objects.filter(owner=self.recipient).exists())
        self.assertFalse(GiftCard.objects.filter(owner=self.sender).exists())

    def test_cannot_send_revealed_card(self):
        card = make_card(self.sender, status=GiftCard.STATUS_REVEALED, redeemable=False)
        res = self.client.post(reverse("p2p:send-giftcard"), {
            "tag": "giftrecipient", "card_id": card.id, "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 400)
        card.refresh_from_db()
        self.assertEqual(card.owner_id, self.sender.id)

    def test_cannot_send_others_card(self):
        other = cp_user("other@cardpulse.test", "other")
        card = make_card(other)
        res = self.client.post(reverse("p2p:send-giftcard"), {
            "tag": "giftrecipient", "card_id": card.id, "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 404)


class HistoryTests(APITestCase):
    def test_history_shows_direction_and_counterparty(self):
        sender = cp_user("h1@cardpulse.test", "hist1")
        recipient = cp_user("h2@cardpulse.test", "hist2")
        fund(sender, 5000)
        self.client.post(reverse("p2p:send-cash"), {
            "tag": "hist2", "amount": "1000", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(sender))

        res_out = self.client.get(reverse("p2p:history"), HTTP_AUTHORIZATION=auth(sender))
        self.assertEqual(res_out.data[0]["direction"], "out")
        self.assertEqual(res_out.data[0]["counterparty"]["tag"], "hist2")

        res_in = self.client.get(reverse("p2p:history"), HTTP_AUTHORIZATION=auth(recipient))
        self.assertEqual(res_in.data[0]["direction"], "in")
        self.assertEqual(res_in.data[0]["counterparty"]["tag"], "hist1")


class CrossCurrencyTransferTests(APITestCase):
    """A GHS sender to an NGN recipient: sender debited GHS, recipient credited
    the NGN equivalent; each side's ledger is in its own currency."""

    def setUp(self):
        self.sender = cp_user("ghsend@cardpulse.test", "ghsend")
        sw = get_or_create_wallet(self.sender)
        sw.currency = "GHS"; sw.balance = Decimal("1000"); sw.save()
        self.sender.refresh_from_db()

        self.recipient = cp_user("ngrecv@cardpulse.test", "ngrecv")
        rw = get_or_create_wallet(self.recipient)
        rw.currency = "NGN"; rw.balance = Decimal("0"); rw.save()

    @patch("p2p.services.convert", return_value=Decimal("4000.00"))  # 200 GHS -> 4000 NGN
    def test_cross_currency_credits_recipient_in_their_currency(self, _conv):
        res = self.client.post(reverse("p2p:send-cash"), {
            "tag": "ngrecv", "amount": "200", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))
        self.assertEqual(res.status_code, 201, res.data)

        self.sender.wallet.refresh_from_db()
        self.recipient.wallet.refresh_from_db()
        self.assertEqual(self.sender.wallet.balance, Decimal("800.00"))   # 1000 - 200 GHS
        self.assertEqual(self.recipient.wallet.balance, Decimal("4000.00"))  # + 4000 NGN

        from cardpulse.models import LedgerEntry
        out = LedgerEntry.objects.get(user=self.sender, kind="transfer_out")
        self.assertEqual((out.amount, out.currency), (Decimal("200.00"), "GHS"))
        inc = LedgerEntry.objects.get(user=self.recipient, kind="transfer_in")
        self.assertEqual((inc.amount, inc.currency), (Decimal("4000.00"), "NGN"))

    @patch("p2p.services.convert", return_value=Decimal("4000.00"))
    def test_history_shows_each_side_its_own_amount(self, _conv):
        self.client.post(reverse("p2p:send-cash"), {
            "tag": "ngrecv", "amount": "200", "pin": "1234",
        }, format="json", HTTP_AUTHORIZATION=auth(self.sender))

        s_hist = self.client.get(reverse("p2p:history"), HTTP_AUTHORIZATION=auth(self.sender)).data
        self.assertEqual(s_hist[0]["direction"], "out")
        self.assertEqual(str(s_hist[0]["amount_ngn"]), "200.00")
        self.assertEqual(s_hist[0]["currency"], "GHS")

        r_hist = self.client.get(reverse("p2p:history"), HTTP_AUTHORIZATION=auth(self.recipient)).data
        self.assertEqual(r_hist[0]["direction"], "in")
        self.assertEqual(str(r_hist[0]["amount_ngn"]), "4000.00")
        self.assertEqual(r_hist[0]["currency"], "NGN")
