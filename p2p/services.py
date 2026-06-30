"""
P2P services — lookup a friend by tag, send cash, send a giftcard.

Every money/asset movement is atomic and PIN-gated, with the wallet/card rows
locked (select_for_update) so concurrent sends can't race.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction

from cardpulse.services import find_user_by_tag, record_ledger, record_audit
from giftcards.models import GiftCard
from users.models import Wallet

from .models import Transfer


class P2PError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _amount(value) -> Decimal:
    try:
        amt = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise P2PError("Invalid amount.")
    if amt <= 0:
        raise P2PError("Amount must be greater than zero.")
    return amt.quantize(Decimal("0.01"))


def _require_pin(user, pin):
    if not user.has_transaction_pin:
        raise P2PError("Set a transaction PIN before sending.", status=400)
    if not user.check_transaction_pin(pin):
        raise P2PError("Incorrect transaction PIN.", status=403)


def _resolve_recipient(sender, tag):
    recipient = find_user_by_tag(tag)
    if not recipient:
        raise P2PError("No CardPulse user with that tag.", status=404)
    if recipient.id == sender.id:
        raise P2PError("You can't send to yourself.", status=400)
    return recipient


def lookup(tag):
    user = find_user_by_tag(tag)
    if not user:
        return {"found": False, "tag": (tag or "").lstrip("@").lower()}
    return {"found": True, "tag": user.tag, "name": user.full_name}


def send_cash(sender, tag, amount, pin, *, note="", ip=None) -> Transfer:
    _require_pin(sender, pin)
    recipient = _resolve_recipient(sender, tag)
    amount = _amount(amount)

    with transaction.atomic():
        # Lock both wallets in a consistent order to avoid deadlock.
        wallets = {
            w.user_id: w
            for w in Wallet.objects.select_for_update().filter(
                user_id__in=[sender.id, recipient.id]
            ).order_by("user_id")
        }
        sw = wallets.get(sender.id)
        rw = wallets.get(recipient.id)
        if sw is None or rw is None:
            raise P2PError("Wallet not found.", status=404)
        if Decimal(str(sw.balance)) < amount:
            raise P2PError("Insufficient wallet balance.", status=402)

        sw.balance = Decimal(str(sw.balance)) - amount
        rw.balance = Decimal(str(rw.balance)) + amount
        sw.save(update_fields=["balance"])
        rw.save(update_fields=["balance"])

        transfer = Transfer.objects.create(
            sender=sender, recipient=recipient, kind=Transfer.KIND_CASH,
            amount_ngn=amount, note=note[:140],
        )
        record_ledger(sender, "debit", "transfer_out", amount, balance_after=sw.balance,
                      reference=f"transfer:{transfer.id}", description=f"To @{recipient.tag}")
        record_ledger(recipient, "credit", "transfer_in", amount, balance_after=rw.balance,
                      reference=f"transfer:{transfer.id}", description=f"From @{sender.tag}")

    record_audit("p2p_send_cash", user=sender, ip_address=ip,
                 detail=f"{amount} -> @{recipient.tag}", metadata={"transfer": transfer.id})
    return transfer


def send_giftcard(sender, tag, card_id, pin, *, note="", ip=None) -> Transfer:
    _require_pin(sender, pin)
    recipient = _resolve_recipient(sender, tag)

    with transaction.atomic():
        try:
            card = GiftCard.objects.select_for_update().get(id=card_id, owner=sender)
        except GiftCard.DoesNotExist:
            raise P2PError("Card not found.", status=404)
        if card.status != GiftCard.STATUS_OWNED or not card.redeemable:
            raise P2PError("This card can't be sent (already revealed, traded, or pending).",
                           status=400)

        card.owner = recipient
        card.save(update_fields=["owner"])

        transfer = Transfer.objects.create(
            sender=sender, recipient=recipient, kind=Transfer.KIND_GIFTCARD,
            card=card, note=note[:140],
        )

    record_audit("p2p_send_giftcard", user=sender, ip_address=ip,
                 detail=f"card:{card.id} -> @{recipient.tag}", metadata={"transfer": transfer.id})
    return transfer
