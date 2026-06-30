"""
Banking services — fund the wallet (deposit) and cash out to a bank (withdraw).

Withdrawal saga: verify account -> create recipient -> debit (hold) + record ->
initiate Paystack transfer. On any provider failure, or a transfer.failed /
transfer.reversed webhook, we auto-refund exactly once.
"""
import os
import uuid
from decimal import Decimal, InvalidOperation

import requests
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from cardpulse.services import get_rate_config, record_ledger, record_audit
from common.providers import get_payout_provider, ProviderError
from common.fx import convert, FxError
from common.currencies import quantize
from users.models import Wallet

from .models import Withdrawal

# Paystack settles in NGN. Wallets may be in another currency, so deposits and
# withdrawals convert to/from NGN at this boundary (NGN wallets: identity).
WITHDRAWAL_MIN_NGN = Decimal("1000")
WITHDRAWAL_MIN = WITHDRAWAL_MIN_NGN  # back-compat alias
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")


def _wallet_currency(user):
    w = getattr(user, "wallet", None)
    return (getattr(w, "currency", None) or "NGN") if w else "NGN"


class BankingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _amount(value) -> Decimal:
    try:
        amt = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BankingError("Invalid amount.")
    return amt.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Bank list + account resolution
# --------------------------------------------------------------------------- #
def list_banks():
    cached = None
    try:
        cached = cache.get("cardpulse:banks")
    except Exception:
        pass
    if cached is not None:
        return cached
    try:
        rows = get_payout_provider().list_banks()
    except ProviderError:
        rows = []
    banks = [{"name": b.get("name"), "code": b.get("code")} for b in rows if isinstance(b, dict)]
    try:
        cache.set("cardpulse:banks", banks, 86400)
    except Exception:
        pass
    return banks


def resolve_account(account_number, bank_code):
    try:
        data = get_payout_provider().resolve_account(account_number, bank_code)
    except ProviderError as exc:
        raise BankingError(f"Could not verify account: {exc}", status=502)
    if not isinstance(data, dict) or not data.get("status"):
        raise BankingError("Could not verify that account number / bank.", status=400)
    return (data.get("data") or {}).get("account_name", "")


# --------------------------------------------------------------------------- #
# Withdrawal
# --------------------------------------------------------------------------- #
def _refund(withdrawal, reason="Withdrawal refund"):
    """Credit the held amount back to the wallet — exactly once."""
    with transaction.atomic():
        wd = Withdrawal.objects.select_for_update().get(pk=withdrawal.pk)
        if wd.refunded:
            return
        wallet = Wallet.objects.select_for_update().get(user=wd.user)
        wallet.balance = F("balance") + wd.amount
        wallet.save(update_fields=["balance"])
        wallet.refresh_from_db(fields=["balance"])
        wd.refunded = True
        wd.save(update_fields=["refunded"])
        record_ledger(wd.user, "credit", "reversal", wd.amount, currency=wd.currency,
                      balance_after=wallet.balance,
                      reference=f"withdrawal:{wd.id}", description=reason)


def initiate_withdrawal(user, amount, bank_code, account_number, pin, *,
                        idempotency_key=None, ip=None):
    if not user.has_transaction_pin:
        raise BankingError("Set a transaction PIN first.", status=400)
    if not user.check_transaction_pin(pin):
        raise BankingError("Incorrect transaction PIN.", status=403)

    amount = _amount(amount)  # in the user's wallet currency
    wcur = _wallet_currency(user)
    try:
        amount_ngn = convert(amount, wcur, "NGN")  # what Paystack will transfer
    except FxError:
        raise BankingError("This service is temporarily unavailable. Please try again later.",
                           status=503)
    if amount_ngn < WITHDRAWAL_MIN_NGN:
        min_wcur = quantize(convert(WITHDRAWAL_MIN_NGN, "NGN", wcur), wcur) if wcur != "NGN" else WITHDRAWAL_MIN_NGN
        raise BankingError(f"Minimum withdrawal is {min_wcur} {wcur}.")

    key = (idempotency_key or "").strip() or uuid.uuid4().hex
    existing = Withdrawal.objects.filter(idempotency_key=key).first()
    if existing:
        return existing

    # Verify the destination account and register a transfer recipient first.
    account_name = resolve_account(account_number, bank_code)
    try:
        rec = get_payout_provider().create_recipient(account_name or user.full_name,
                                                     account_number, bank_code)
    except ProviderError as exc:
        raise BankingError(f"Could not set up payout recipient: {exc}", status=502)
    recipient_code = (rec.get("data") or {}).get("recipient_code") if isinstance(rec, dict) else None
    if not recipient_code:
        raise BankingError("Could not set up payout recipient.", status=502)

    cfg = get_rate_config()
    threshold = Decimal(str(cfg.manual_review_threshold or 0))
    reference = f"cpw_{uuid.uuid4().hex}"

    # Debit (hold) the funds and create the record atomically.
    try:
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
            if Decimal(str(wallet.balance)) < amount:
                raise BankingError("Insufficient wallet balance.", status=402)
            wallet.balance = F("balance") - amount
            wallet.save(update_fields=["balance"])
            wallet.refresh_from_db(fields=["balance"])

            # The manual-review threshold is configured in NGN — compare the NGN payout.
            review = threshold > 0 and amount_ngn >= threshold
            wd = Withdrawal.objects.create(
                user=user, amount=amount, currency=wcur, amount_ngn=amount_ngn,
                bank_code=bank_code, account_number=account_number,
                account_name=account_name, recipient_code=recipient_code, reference=reference,
                idempotency_key=key,
                status=Withdrawal.STATUS_PENDING_REVIEW if review else Withdrawal.STATUS_PROCESSING,
            )
            record_ledger(user, "debit", "withdrawal", amount, currency=wcur,
                          balance_after=wallet.balance,
                          reference=f"withdrawal:{wd.id}", description=f"To {bank_code}/{account_number}")
    except BankingError:
        raise

    if wd.status == Withdrawal.STATUS_PENDING_REVIEW:
        record_audit("withdrawal_review", user=user, ip_address=ip, metadata={"withdrawal": wd.id})
        return wd

    _send_transfer(wd)
    record_audit("withdrawal_initiated", user=user, ip_address=ip, metadata={"withdrawal": wd.id})
    return wd


def _send_transfer(wd):
    """Call Paystack to actually move the money. Refund on hard failure."""
    # Paystack transfers in NGN — use the converted NGN amount, not the wallet amount.
    transfer_ngn = wd.amount_ngn if wd.amount_ngn and wd.amount_ngn > 0 else wd.amount
    try:
        resp = get_payout_provider().initiate_transfer(
            wd.recipient_code, transfer_ngn, wd.reference, reason="CardPulse withdrawal"
        )
    except ProviderError as exc:
        wd.status = Withdrawal.STATUS_FAILED
        wd.error = str(exc)[:255]
        wd.save(update_fields=["status", "error"])
        _refund(wd, "Refund: transfer could not be initiated")
        return wd

    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    if not data:
        wd.status = Withdrawal.STATUS_FAILED
        wd.error = str(resp)[:255]
        wd.save(update_fields=["status", "error"])
        _refund(wd, "Refund: transfer rejected")
        return wd

    wd.transfer_code = data.get("transfer_code", "")
    pstatus = str(data.get("status") or "").lower()
    if pstatus == "success":
        wd.status = Withdrawal.STATUS_SUCCESS
    elif pstatus == "failed":
        wd.status = Withdrawal.STATUS_FAILED
        wd.save(update_fields=["transfer_code", "status"])
        _refund(wd, "Refund: transfer failed")
        return wd
    else:  # pending / otp / processing — webhook will finalize
        wd.status = Withdrawal.STATUS_PROCESSING
    wd.save(update_fields=["transfer_code", "status"])
    return wd


def handle_transfer_event(event, data):
    """Process a Paystack transfer.* webhook event for a withdrawal."""
    if not isinstance(data, dict):
        return
    code = data.get("transfer_code")
    reference = data.get("reference")
    wd = None
    if code:
        wd = Withdrawal.objects.filter(transfer_code=code).first()
    if not wd and reference:
        wd = Withdrawal.objects.filter(reference=reference).first()
    if not wd:
        return

    if event == "transfer.success":
        if wd.status != Withdrawal.STATUS_SUCCESS:
            wd.status = Withdrawal.STATUS_SUCCESS
            wd.save(update_fields=["status"])
    elif event == "transfer.failed":
        wd.status = Withdrawal.STATUS_FAILED
        wd.save(update_fields=["status"])
        _refund(wd, "Refund: transfer failed")
    elif event == "transfer.reversed":
        wd.status = Withdrawal.STATUS_REVERSED
        wd.save(update_fields=["status"])
        _refund(wd, "Refund: transfer reversed")


def approve_withdrawal(admin, withdrawal_id):
    wd = Withdrawal.objects.get(pk=withdrawal_id)
    if wd.status != Withdrawal.STATUS_PENDING_REVIEW:
        raise BankingError("Withdrawal is not pending review.", status=400)
    wd.reviewer = admin
    wd.save(update_fields=["reviewer"])
    _send_transfer(wd)
    record_audit("withdrawal_approved", user=admin, metadata={"withdrawal": wd.id})
    return wd


def reject_withdrawal(admin, withdrawal_id, reason=""):
    wd = Withdrawal.objects.get(pk=withdrawal_id)
    if wd.status != Withdrawal.STATUS_PENDING_REVIEW:
        raise BankingError("Withdrawal is not pending review.", status=400)
    wd.status = Withdrawal.STATUS_FAILED
    wd.reviewer = admin
    wd.error = (reason or "Rejected by admin")[:255]
    wd.save(update_fields=["status", "reviewer", "error"])
    _refund(wd, "Refund: withdrawal rejected")
    record_audit("withdrawal_rejected", user=admin, metadata={"withdrawal": wd.id})
    return wd


# --------------------------------------------------------------------------- #
# Deposit (fund wallet) — thin wrapper over Paystack, reuses payments.Deposit
# --------------------------------------------------------------------------- #
def create_deposit(user, amount, *, callback_url=None):
    from payments.models import Deposit

    amount = _amount(amount)  # in the user's wallet currency
    wcur = _wallet_currency(user)
    try:
        charge_ngn = convert(amount, wcur, "NGN")
    except FxError:
        raise BankingError("This service is temporarily unavailable. Please try again later.",
                           status=503)
    if charge_ngn < WITHDRAWAL_MIN_NGN:
        min_wcur = quantize(convert(WITHDRAWAL_MIN_NGN, "NGN", wcur), wcur) if wcur != "NGN" else WITHDRAWAL_MIN_NGN
        raise BankingError(f"Minimum deposit is {min_wcur} {wcur}.")

    deposit = Deposit.objects.create(user=user, amount=amount, currency=wcur,
                                     method="paystack", status="pending",
                                     provider_payload={"charge_ngn": str(charge_ngn)})
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
    body = {
        "email": user.email,
        "amount": int(charge_ngn * 100),
        "currency": "NGN",
        "metadata": {"deposit_id": str(deposit.id), "user_id": str(user.id), "app": "cardpulse"},
    }
    if callback_url:
        body["callback_url"] = callback_url
    try:
        r = requests.post("https://api.paystack.co/transaction/initialize",
                          json=body, headers=headers, timeout=20)
        resp = r.json()
    except Exception as exc:
        deposit.status = "failed"
        deposit.save(update_fields=["status"])
        raise BankingError(f"Could not start payment: {exc}", status=502)

    if not resp.get("status"):
        deposit.status = "failed"
        deposit.save(update_fields=["status"])
        raise BankingError(resp.get("message", "Could not start payment."), status=502)

    deposit.provider_payload = {**(deposit.provider_payload or {}), "init": resp}
    deposit.provider_reference = resp["data"]["reference"]
    deposit.save(update_fields=["provider_payload", "provider_reference"])
    return {
        "authorization_url": resp["data"]["authorization_url"],
        "reference": resp["data"]["reference"],
        "deposit_id": str(deposit.id),
    }
