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

from cardpulse.services import get_rate_config

logger = logging.getLogger(__name__)

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
