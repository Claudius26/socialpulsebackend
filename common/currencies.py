"""
Supported countries + currencies, with the EXACT decimal precision each
currency uses. Getting decimals right is what prevents money from being lost
in rounding (e.g. XOF/XAF/UGX have NO minor unit — amounts must be whole).
"""
from decimal import Decimal, ROUND_HALF_UP

# Countries the platform serves (registration is limited to these for now).
COUNTRIES = [
    {"name": "Nigeria", "code": "NG", "currency": "NGN", "flag": "🇳🇬"},
    {"name": "Ghana", "code": "GH", "currency": "GHS", "flag": "🇬🇭"},
    {"name": "Kenya", "code": "KE", "currency": "KES", "flag": "🇰🇪"},
    {"name": "South Africa", "code": "ZA", "currency": "ZAR", "flag": "🇿🇦"},
    {"name": "Cameroon", "code": "CM", "currency": "XAF", "flag": "🇨🇲"},
    {"name": "Togo", "code": "TG", "currency": "XOF", "flag": "🇹🇬"},
    {"name": "Côte d'Ivoire", "code": "CI", "currency": "XOF", "flag": "🇨🇮"},
    {"name": "Senegal", "code": "SN", "currency": "XOF", "flag": "🇸🇳"},
    {"name": "Benin", "code": "BJ", "currency": "XOF", "flag": "🇧🇯"},
    {"name": "Uganda", "code": "UG", "currency": "UGX", "flag": "🇺🇬"},
]

# Minor-unit digits per currency. Default 2 if unknown.
CURRENCY_DECIMALS = {
    "NGN": 2, "GHS": 2, "KES": 2, "ZAR": 2, "USD": 2, "EUR": 2, "GBP": 2, "EGP": 2,
    "XOF": 0, "XAF": 0, "UGX": 0, "RWF": 0, "JPY": 0,
}

CURRENCY_SYMBOLS = {
    "NGN": "₦", "GHS": "₵", "KES": "KSh", "ZAR": "R", "USD": "$",
    "XOF": "CFA", "XAF": "FCFA", "UGX": "USh", "EGP": "E£",
}

# Currencies Paystack can both collect AND pay out reliably. Used to gate
# deposits/withdrawals so users never hit a dead end.
PAYSTACK_CURRENCIES = {"NGN", "GHS", "ZAR", "KES", "USD"}

_BY_NAME = {c["name"].lower(): c for c in COUNTRIES}
_BY_CODE = {c["code"].lower(): c for c in COUNTRIES}


def currency_for_country(country) -> str:
    """Map a country name or ISO code to its currency. Defaults to NGN."""
    if not country:
        return "NGN"
    key = str(country).strip().lower()
    entry = _BY_NAME.get(key) or _BY_CODE.get(key)
    return entry["currency"] if entry else "NGN"


def decimals_for(currency: str) -> int:
    return CURRENCY_DECIMALS.get((currency or "NGN").upper(), 2)


def quantize(amount, currency: str) -> Decimal:
    """Round an amount to the currency's exact precision (HALF_UP).

    Whole-unit currencies (XOF/XAF/UGX) round to integers; others to 2 dp.
    Using a single, consistent rounding rule everywhere keeps the books exact.
    """
    d = decimals_for(currency)
    quant = Decimal(1) if d == 0 else Decimal(10) ** -d
    try:
        return Decimal(str(amount)).quantize(quant, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")
