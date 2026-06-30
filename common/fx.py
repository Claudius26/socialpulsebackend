"""
Currency conversion. One source of truth for rates + rounding.

Safety rule: in money paths we NEVER guess. If a live rate can't be obtained,
`get_rate` raises FxError so the caller can abort cleanly instead of charging /
crediting at a wrong rate.
"""
import logging
import os
from decimal import Decimal

import requests
from django.core.cache import cache

from .currencies import quantize

logger = logging.getLogger(__name__)

FX_TTL = 900  # 15 min


class FxError(Exception):
    """Raised when a conversion rate can't be obtained."""


def get_rate(from_cur: str, to_cur: str) -> Decimal:
    from_cur = (from_cur or "NGN").upper()
    to_cur = (to_cur or "NGN").upper()
    if from_cur == to_cur:
        return Decimal("1")

    key = f"fx:{from_cur}:{to_cur}"
    try:
        cached = cache.get(key)
        if cached is not None:
            return Decimal(str(cached))
    except Exception:
        pass

    api_key = os.getenv("EXCHANGE_RATE_API_KEY")
    rate = None
    if api_key:
        try:
            resp = requests.get(
                f"https://v6.exchangerate-api.com/v6/{api_key}/pair/{from_cur}/{to_cur}",
                timeout=10,
            )
            data = resp.json()
            if data.get("result") == "success" and data.get("conversion_rate"):
                rate = Decimal(str(data["conversion_rate"]))
        except Exception as exc:
            logger.warning("FX %s->%s failed: %s", from_cur, to_cur, exc)

    if rate is None or rate <= 0:
        raise FxError(f"No conversion rate available for {from_cur} -> {to_cur}")

    try:
        cache.set(key, str(rate), FX_TTL)
    except Exception:
        pass
    return rate


def convert(amount, from_cur: str, to_cur: str) -> Decimal:
    """Convert `amount` from one currency to another, rounded to the TARGET
    currency's exact precision (so whole-unit currencies stay whole)."""
    from_cur = (from_cur or "NGN").upper()
    to_cur = (to_cur or "NGN").upper()
    if from_cur == to_cur:
        return quantize(amount, to_cur)
    rate = get_rate(from_cur, to_cur)
    return quantize(Decimal(str(amount)) * rate, to_cur)
