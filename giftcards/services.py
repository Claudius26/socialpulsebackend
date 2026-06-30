"""
Giftcard catalog services — FX + normalization + pricing.

The app never sees provider internals or our margin. We take Reloadly's raw
product data, convert each denomination to NGN, apply the (hidden) buy markup
from RateConfig, and return a clean catalog with final NGN prices only.
"""
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.core.cache import cache
from django.db import transaction, IntegrityError
from django.db.models import F

from cardpulse.services import get_rate_config, record_ledger, record_profit, record_audit
from cardpulse.crypto import encrypt, decrypt

logger = logging.getLogger(__name__)


class GiftcardError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status

FX_TTL = 900  # 15 min
CATALOG_TTL = 600  # 10 min

# Conservative fallbacks if the FX API is unreachable (kept sane, not exact).
FX_FALLBACK_TO_NGN = {
    "USD": Decimal("1600"),
    "EUR": Decimal("1750"),
    "GBP": Decimal("2050"),
    "NGN": Decimal("1"),
}


def _money(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def currency_to_ngn_rate(currency: str) -> Decimal:
    """How many NGN one unit of `currency` is worth. Cached per base currency."""
    currency = (currency or "USD").upper()
    if currency == "NGN":
        return Decimal("1")

    cache_key = f"cardpulse:fx:{currency}:NGN"
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return Decimal(str(cached))
    except Exception:
        pass

    api_key = os.getenv("EXCHANGE_RATE_API_KEY")
    rate = None
    if api_key:
        try:
            resp = requests.get(
                f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{currency}", timeout=10
            )
            data = resp.json()
            ngn = (data.get("conversion_rates") or {}).get("NGN")
            if ngn:
                rate = Decimal(str(ngn))
        except Exception as exc:
            logger.warning("FX fetch failed for %s->NGN: %s", currency, exc)

    if rate is None:
        rate = FX_FALLBACK_TO_NGN.get(currency, FX_FALLBACK_TO_NGN["USD"])

    try:
        cache.set(cache_key, str(rate), FX_TTL)
    except Exception:
        pass
    return rate


def price_ngn(amount, currency: str, *, rate: Decimal = None, markup: Decimal = None) -> float:
    """Final NGN price for `amount` of `currency`, with the hidden buy markup.

    The breakdown (rate, markup) is NEVER returned to the client — only this.
    """
    amount = _money(amount)
    if rate is None:
        rate = currency_to_ngn_rate(currency)
    if markup is None:
        markup = get_rate_config().buy_markup_rate
    gross = amount * rate * (Decimal("1") + _money(markup))
    return float(gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_product(product: dict, *, markup: Decimal = None) -> dict:
    """Reloadly product -> clean catalog entry with NGN-priced denominations."""
    currency = product.get("recipientCurrencyCode") or product.get("senderCurrencyCode") or "USD"
    rate = currency_to_ngn_rate(currency)
    if markup is None:
        markup = get_rate_config().buy_markup_rate

    denom_type = product.get("denominationType") or "FIXED"
    logos = product.get("logoUrls") or []
    brand = product.get("brand") or {}
    country = product.get("country") or {}

    entry = {
        "product_id": product.get("productId"),
        "name": product.get("productName"),
        "brand": brand.get("brandName"),
        "currency": currency,
        "denomination_type": denom_type,
        "logo": logos[0] if logos else None,
        "country": {
            "iso": country.get("isoName"),
            "name": country.get("name"),
            "flag": country.get("flagUrl"),
        },
    }

    if denom_type == "RANGE":
        lo = _money(product.get("minRecipientDenomination"))
        hi = _money(product.get("maxRecipientDenomination"))
        entry["range"] = {
            "min": float(lo),
            "max": float(hi),
            "min_price_ngn": price_ngn(lo, currency, rate=rate, markup=markup),
            "max_price_ngn": price_ngn(hi, currency, rate=rate, markup=markup),
        }
        entry["denominations"] = []
    else:
        fixed = product.get("fixedRecipientDenominations") or []
        entry["denominations"] = [
            {"value": float(_money(v)), "price_ngn": price_ngn(v, currency, rate=rate, markup=markup)}
            for v in fixed
        ]
        entry["range"] = None

    redeem = product.get("redeemInstruction") or {}
    entry["redeem_instruction"] = redeem.get("concise") or ""
    return entry


def fetch_catalog(*, country=None, page=1, size=50, search=None) -> dict:
    """Normalized, cached catalog page from the giftcard provider."""
    from common.providers import get_giftcard_provider, ProviderError

    cache_key = f"cardpulse:catalog:{country or 'all'}:{page}:{size}:{(search or '').lower()}"
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    except Exception:
        pass

    try:
        raw = get_giftcard_provider().list_products(
            country=country, page=page, size=size, product_name=search
        )
    except ProviderError as exc:
        raise exc

    content = raw.get("content") if isinstance(raw, dict) else None
    products = content if isinstance(content, list) else (raw if isinstance(raw, list) else [])

    markup = get_rate_config().buy_markup_rate
    result = {
        "page": raw.get("pageNumber", page) if isinstance(raw, dict) else page,
        "total_pages": raw.get("totalPages") if isinstance(raw, dict) else None,
        "total": raw.get("totalElements") if isinstance(raw, dict) else len(products),
        "products": [normalize_product(p, markup=markup) for p in products],
    }
    try:
        cache.set(cache_key, result, CATALOG_TTL)
    except Exception:
        pass
    return result


# --------------------------------------------------------------------------- #
# Purchase (mint) — the first real money flow
# --------------------------------------------------------------------------- #
def _resolve_purchase(product: dict, face_value: Decimal):
    """Validate the chosen denomination and return (recipient_ccy, unit_price).

    unit_price is what we pay the provider, in the provider's sender currency.
    """
    recipient_ccy = product.get("recipientCurrencyCode") or product.get("senderCurrencyCode") or "USD"
    denom_type = (product.get("denominationType") or "FIXED").upper()
    fv = _money(face_value)

    if denom_type == "RANGE":
        lo = _money(product.get("minRecipientDenomination"))
        hi = _money(product.get("maxRecipientDenomination"))
        if fv < lo or fv > hi:
            raise GiftcardError(f"Amount must be between {lo} and {hi} {recipient_ccy}.")
        return recipient_ccy, fv

    fixed = [_money(v) for v in (product.get("fixedRecipientDenominations") or [])]
    if fv not in fixed:
        raise GiftcardError("That denomination is not available for this product.")

    smap = product.get("fixedRecipientToSenderDenominationsMap") or {}
    # Reloadly maps by recipient-denomination string ("10" or "10.0").
    sender = smap.get(str(int(fv))) if fv == fv.to_integral_value() else None
    sender = sender or smap.get(str(fv)) or smap.get(f"{fv:.1f}")
    return recipient_ccy, _money(sender) if sender is not None else fv


def _parse_order(resp: dict):
    """Return (transaction_id, ok) from a Reloadly order response."""
    if not isinstance(resp, dict):
        return None, False
    tx_id = resp.get("transactionId") or resp.get("transactionCreatedTime") and resp.get("transactionId")
    status = str(resp.get("status") or "").upper()
    failed = status in ("FAILED", "REFUNDED", "ERROR")
    return resp.get("transactionId"), bool(resp.get("transactionId")) and not failed


def _parse_code(resp):
    """Extract (code, pin) from a redeem-code response (list or dict)."""
    item = None
    if isinstance(resp, list) and resp:
        item = resp[0]
    elif isinstance(resp, dict):
        # Some responses wrap cards under a key; otherwise treat dict as the card.
        if isinstance(resp.get("cardNumber"), str) or resp.get("pinCode"):
            item = resp
        else:
            for v in resp.values():
                if isinstance(v, list) and v:
                    item = v[0]
                    break
    if not isinstance(item, dict):
        return "", ""
    code = item.get("cardNumber") or item.get("cardCode") or item.get("code") or ""
    pin = item.get("pinCode") or item.get("pin") or ""
    return str(code), str(pin)


def purchase_giftcard(user, product_id, face_value, *, idempotency_key, ip=None):
    """Buy (mint) a giftcard, charging the user's cash wallet.

    Saga: debit + order row (atomic) -> provider order + code (network) ->
    create encrypted card / complete order, OR refund on provider failure.
    Idempotent on idempotency_key so retries never double-charge.
    """
    from users.models import Wallet
    from common.providers import get_giftcard_provider, ProviderError
    from .models import GiftCard, GiftCardOrder

    existing = GiftCardOrder.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing

    try:
        product = get_giftcard_provider().get_product(product_id)
    except ProviderError as exc:
        raise GiftcardError(f"Giftcard provider unavailable: {exc}", status=502)
    if not isinstance(product, dict) or not product.get("productId"):
        raise GiftcardError("Product not found", status=404)

    recipient_ccy, unit_price = _resolve_purchase(product, face_value)
    fv = _money(face_value)
    markup = get_rate_config().buy_markup_rate
    amount_ngn = Decimal(str(price_ngn(fv, recipient_ccy, markup=markup)))
    cost_ngn = (unit_price * currency_to_ngn_rate(
        product.get("senderCurrencyCode") or recipient_ccy
    )).quantize(Decimal("0.01"))

    # Step A — reserve funds + create the order atomically.
    try:
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
            if _money(wallet.balance) < amount_ngn:
                raise GiftcardError("Insufficient wallet balance.", status=402)
            wallet.balance = F("balance") - amount_ngn
            wallet.save(update_fields=["balance"])
            wallet.refresh_from_db(fields=["balance"])
            order = GiftCardOrder.objects.create(
                user=user, product_id=product["productId"],
                product_name=product.get("productName", ""), face_value=fv,
                currency=recipient_ccy, unit_price=unit_price, amount_ngn=amount_ngn,
                idempotency_key=idempotency_key, status=GiftCardOrder.STATUS_PENDING,
            )
            record_ledger(user, "debit", "giftcard_purchase", amount_ngn,
                          balance_after=wallet.balance, reference=f"order:{order.id}",
                          description=product.get("productName", ""))
    except IntegrityError:
        # Concurrent duplicate with same idempotency_key — return the winner.
        return GiftCardOrder.objects.get(idempotency_key=idempotency_key)

    # Step B — place the order with the provider (network).
    provider = get_giftcard_provider()
    try:
        resp = provider.order(product["productId"], unit_price, quantity=1,
                              custom_identifier=idempotency_key)
        tx_id, ok = _parse_order(resp)
        if not ok:
            raise ProviderError(f"Order rejected: {resp}")
        code, pin = _parse_code(provider.redeem_code(tx_id)) if tx_id else ("", "")
    except ProviderError as exc:
        # Step B-fail — refund and mark the order failed.
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
            wallet.balance = F("balance") + amount_ngn
            wallet.save(update_fields=["balance"])
            wallet.refresh_from_db(fields=["balance"])
            record_ledger(user, "credit", "reversal", amount_ngn,
                          balance_after=wallet.balance, reference=f"order:{order.id}",
                          description="Refund: giftcard purchase failed")
            order.status = GiftCardOrder.STATUS_FAILED
            order.error = str(exc)[:255]
            order.save(update_fields=["status", "error"])
        record_audit("giftcard_purchase_failed", user=user, ip_address=ip,
                     detail=str(exc)[:255], metadata={"order": order.id})
        return order

    # Step C — create the encrypted card and complete the order.
    with transaction.atomic():
        card = GiftCard.objects.create(
            owner=user, product_id=product["productId"],
            product_name=product.get("productName", ""),
            brand=(product.get("brand") or {}).get("brandName", ""),
            country=(product.get("country") or {}).get("isoName", ""),
            currency=recipient_ccy, face_value=fv, face_value_ngn=amount_ngn,
            cost_ngn=cost_ngn, code_encrypted=encrypt(code), pin_encrypted=encrypt(pin),
            status=GiftCard.STATUS_OWNED if code else GiftCard.STATUS_PROCESSING,
            source=GiftCard.SOURCE_MINTED, redeemable=bool(code),
            reloadly_transaction_id=str(tx_id or ""), custom_identifier=idempotency_key,
        )
        order.card = card
        order.reloadly_transaction_id = str(tx_id or "")
        order.status = GiftCardOrder.STATUS_COMPLETED
        order.save(update_fields=["card", "reloadly_transaction_id", "status"])

        # Buy-side margin (only if a markup is configured): amount charged - our cost.
        margin = (amount_ngn - cost_ngn)
        if margin > 0:
            record_profit(margin, user=user, source="buy_markup",
                          reference=f"order:{order.id}")

    record_audit("giftcard_purchase", user=user, ip_address=ip,
                 detail=product.get("productName", ""), metadata={"order": order.id, "card": card.id})
    return order


def reveal_card(user, card_id, pin, *, ip=None):
    """Decrypt and return a card's secret to its owner. Requires the txn PIN.

    Revealing makes the card NON-tradeable (the owner now holds the live code),
    which is what prevents a card from being both spent and cashed out.
    """
    from .models import GiftCard

    if not user.check_transaction_pin(pin):
        raise GiftcardError("Incorrect transaction PIN.", status=403)

    card = GiftCard.objects.filter(id=card_id, owner=user).first()
    if not card:
        raise GiftcardError("Card not found.", status=404)
    if card.status not in (GiftCard.STATUS_OWNED, GiftCard.STATUS_REVEALED):
        raise GiftcardError("This card cannot be revealed.", status=400)
    if not card.has_code:
        raise GiftcardError("This card's code is still being issued. Try again shortly.", status=409)

    with transaction.atomic():
        locked = GiftCard.objects.select_for_update().get(pk=card.pk)
        locked.status = GiftCard.STATUS_REVEALED
        locked.redeemable = False
        locked.save(update_fields=["status", "redeemable"])

    record_audit("giftcard_reveal", user=user, ip_address=ip, metadata={"card": card.id})
    return {
        "id": card.id,
        "product_name": card.product_name,
        "currency": card.currency,
        "face_value": float(card.face_value),
        "code": decrypt(card.code_encrypted),
        "pin": decrypt(card.pin_encrypted),
    }


# --------------------------------------------------------------------------- #
# Trade a card for cash — the platform keeps (1 - payout_rate), hidden.
# --------------------------------------------------------------------------- #
from django.utils import timezone  # noqa: E402


def _credit_payout_and_bank_card(user, card, trade):
    """Pay the trader, move the card into platform inventory, record profit."""
    from users.models import Wallet
    from .models import GiftCard

    wallet = Wallet.objects.select_for_update().get(user=user)
    wallet.balance = F("balance") + trade.payout_ngn
    wallet.save(update_fields=["balance"])
    wallet.refresh_from_db(fields=["balance"])

    card.owner = None  # back into platform inventory for resale (recirculation)
    card.status = GiftCard.STATUS_TRADED
    card.redeemable = False
    card.save(update_fields=["owner", "status", "redeemable"])

    record_ledger(user, "credit", "trade_payout", trade.payout_ngn,
                  balance_after=wallet.balance, reference=f"trade:{trade.id}",
                  description=card.product_name)
    record_profit(trade.profit_ngn, user=user, source="trade", reference=f"trade:{trade.id}")


def trade_card(user, card_id, pin, *, ip=None):
    """Cash out a giftcard. Pays payout_rate of the card's market value; the
    rest is platform margin. Auto-completes unless above the review threshold."""
    from .models import GiftCard, GiftCardTrade

    if not user.has_transaction_pin:
        raise GiftcardError("Set a transaction PIN first.", status=400)
    if not user.check_transaction_pin(pin):
        raise GiftcardError("Incorrect transaction PIN.", status=403)

    cfg = get_rate_config()
    rate = Decimal(str(cfg.trade_payout_rate))
    threshold = Decimal(str(cfg.manual_review_threshold or 0))

    with transaction.atomic():
        try:
            card = GiftCard.objects.select_for_update().get(id=card_id, owner=user)
        except GiftCard.DoesNotExist:
            raise GiftcardError("Card not found.", status=404)
        if card.status != GiftCard.STATUS_OWNED or not card.redeemable:
            raise GiftcardError("This card can't be traded (revealed, pending, or already traded).",
                                status=400)
        if not card.has_code:
            raise GiftcardError("This card's code is still being issued. Try again shortly.", status=409)

        value = (Decimal(str(card.face_value)) * currency_to_ngn_rate(card.currency)).quantize(Decimal("0.01"))
        payout = (value * rate).quantize(Decimal("0.01"))
        profit = (value - payout).quantize(Decimal("0.01"))

        trade = GiftCardTrade(
            user=user, card=card, face_value=card.face_value, currency=card.currency,
            value_ngn=value, payout_rate=rate, payout_ngn=payout, profit_ngn=profit,
        )

        if threshold > 0 and payout >= threshold:
            # Route to manual review — lock the card, pay nothing yet.
            card.redeemable = False
            card.save(update_fields=["redeemable"])
            trade.status = GiftCardTrade.STATUS_PENDING_REVIEW
            trade.save()
            record_audit("giftcard_trade_review", user=user, ip_address=ip,
                         metadata={"trade": trade.id})
            return trade

        trade.status = GiftCardTrade.STATUS_COMPLETED
        trade.save()
        _credit_payout_and_bank_card(user, card, trade)

    record_audit("giftcard_trade", user=user, ip_address=ip, metadata={"trade": trade.id})
    return trade


def approve_trade(admin, trade_id):
    """Admin approves a queued trade — pays out and banks the card."""
    from .models import GiftCard, GiftCardTrade

    with transaction.atomic():
        trade = GiftCardTrade.objects.select_for_update().get(id=trade_id)
        if trade.status != GiftCardTrade.STATUS_PENDING_REVIEW:
            raise GiftcardError("Trade is not pending review.", status=400)
        card = GiftCard.objects.select_for_update().get(pk=trade.card_id)
        trade.status = GiftCardTrade.STATUS_COMPLETED
        trade.reviewer = admin
        trade.reviewed_at = timezone.now()
        trade.save(update_fields=["status", "reviewer", "reviewed_at"])
        _credit_payout_and_bank_card(trade.user, card, trade)
    record_audit("giftcard_trade_approved", user=admin, metadata={"trade": trade.id})
    return trade


def reject_trade(admin, trade_id, reason=""):
    """Admin rejects a queued trade — unlock the card, no payout."""
    from .models import GiftCard, GiftCardTrade

    with transaction.atomic():
        trade = GiftCardTrade.objects.select_for_update().get(id=trade_id)
        if trade.status != GiftCardTrade.STATUS_PENDING_REVIEW:
            raise GiftcardError("Trade is not pending review.", status=400)
        if trade.card_id:
            card = GiftCard.objects.select_for_update().get(pk=trade.card_id)
            card.redeemable = True
            card.save(update_fields=["redeemable"])
        trade.status = GiftCardTrade.STATUS_REJECTED
        trade.reviewer = admin
        trade.reason = (reason or "")[:255]
        trade.reviewed_at = timezone.now()
        trade.save(update_fields=["status", "reviewer", "reason", "reviewed_at"])
    record_audit("giftcard_trade_rejected", user=admin, metadata={"trade": trade.id})
    return trade

