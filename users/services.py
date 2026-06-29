"""
Wallet service — the single, safe entry point for every money movement.

All balance changes go through here so they are:
  * atomic (wrapped in a transaction),
  * race-safe (the wallet row is locked with select_for_update),
  * consistent (uses F() expressions; never a Python read-modify-write),
  * validated (debits/reserves can never overdraw).

Routing every credit/debit/reserve/release through this module is what makes
the "double charge" / "lost update" class of bug structurally impossible.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F

from common.cache_utils import invalidate_user_wallet_caches
from .models import Wallet


class WalletError(Exception):
    """Base class for wallet errors."""


class InsufficientFunds(WalletError):
    """Raised when a debit/reserve would overdraw the available balance."""


def to_amount(value) -> Decimal:
    """Coerce an incoming amount to a positive Decimal, safely."""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WalletError(f"Invalid amount: {value!r}") from exc
    if amount < 0:
        raise WalletError("Amount must not be negative.")
    return amount


def get_or_create_wallet(user, currency: str = "NGN") -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})
    return wallet


@transaction.atomic
def credit(user, amount) -> Wallet:
    """Add funds to the available balance."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    wallet.balance = F("balance") + amount
    wallet.save(update_fields=["balance"])
    wallet.refresh_from_db(fields=["balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


@transaction.atomic
def debit(user, amount) -> Wallet:
    """Remove funds from the available balance. Raises InsufficientFunds if too low."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance.")
    wallet.balance = F("balance") - amount
    wallet.save(update_fields=["balance"])
    wallet.refresh_from_db(fields=["balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


@transaction.atomic
def reserve(user, amount) -> Wallet:
    """Move funds from available balance into reserved_balance (hold)."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance to reserve.")
    wallet.balance = F("balance") - amount
    wallet.reserved_balance = F("reserved_balance") + amount
    wallet.save(update_fields=["balance", "reserved_balance"])
    wallet.refresh_from_db(fields=["balance", "reserved_balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


@transaction.atomic
def release(user, amount) -> Wallet:
    """Return reserved funds back to the available balance (e.g. on cancel)."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    hold = min(amount, wallet.reserved_balance)  # never release more than is held
    wallet.reserved_balance = F("reserved_balance") - hold
    wallet.balance = F("balance") + hold
    wallet.save(update_fields=["balance", "reserved_balance"])
    wallet.refresh_from_db(fields=["balance", "reserved_balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


@transaction.atomic
def settle_reserved(user, amount) -> Wallet:
    """Consume reserved funds — the money leaves the wallet for good (charge a hold)."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    charge = min(amount, wallet.reserved_balance)
    wallet.reserved_balance = F("reserved_balance") - charge
    wallet.save(update_fields=["reserved_balance"])
    wallet.refresh_from_db(fields=["reserved_balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


# --------------------------------------------------------------------------- #
# API credit pool — a separate balance spent via the public developer API.
# Model: api_available = api_balance - api_reserved_balance.
#   reserve  -> hold funds while a number is pending (api_reserved += amount)
#   charge   -> consume the hold when an SMS lands (api_balance -=, api_reserved -=)
#   release  -> return the hold on cancel (api_reserved -= amount)
# --------------------------------------------------------------------------- #
def api_available(wallet) -> Decimal:
    return (wallet.api_balance or Decimal("0")) - (wallet.api_reserved_balance or Decimal("0"))


@transaction.atomic
def topup_api_credit(user, amount) -> Wallet:
    """Move funds from the main wallet's available balance into the API credit pool."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    if (wallet.balance - wallet.reserved_balance) < amount:
        raise InsufficientFunds("Insufficient wallet balance to fund API credit.")
    wallet.balance = F("balance") - amount
    wallet.api_balance = F("api_balance") + amount
    wallet.save(update_fields=["balance", "api_balance"])
    wallet.refresh_from_db(fields=["balance", "api_balance"])
    invalidate_user_wallet_caches(wallet.user_id)
    return wallet


@transaction.atomic
def reserve_api(user, amount) -> Wallet:
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    if api_available(wallet) < amount:
        raise InsufficientFunds("Insufficient API credit.")
    wallet.api_reserved_balance = F("api_reserved_balance") + amount
    wallet.save(update_fields=["api_reserved_balance"])
    wallet.refresh_from_db(fields=["api_reserved_balance"])
    return wallet


@transaction.atomic
def charge_api(user, amount) -> Wallet:
    """Consume an API-credit hold (money leaves the pool)."""
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    wallet.api_balance = F("api_balance") - amount
    wallet.api_reserved_balance = F("api_reserved_balance") - amount
    wallet.save(update_fields=["api_balance", "api_reserved_balance"])
    wallet.refresh_from_db(fields=["api_balance", "api_reserved_balance"])
    return wallet


@transaction.atomic
def release_api(user, amount) -> Wallet:
    amount = to_amount(amount)
    wallet = Wallet.objects.select_for_update().get(user=user)
    hold = min(amount, wallet.api_reserved_balance)
    wallet.api_reserved_balance = F("api_reserved_balance") - hold
    wallet.save(update_fields=["api_reserved_balance"])
    wallet.refresh_from_db(fields=["api_reserved_balance"])
    return wallet
