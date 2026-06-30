"""
CardPulse foundation services — tags, transaction PIN, ledger/audit/rate.

Views stay thin; all reusable logic lives here so the giftcard / p2p /
withdrawal phases call the same vetted helpers.
"""
import re
import secrets

from django.contrib.auth import get_user_model

from .models import RateConfig, LedgerEntry, ProfitEntry, AuditLog

User = get_user_model()

TAG_RE = re.compile(r"^[a-z0-9_]{3,20}$")
RESERVED_TAGS = {"admin", "support", "cardpulse", "root", "system", "help", "official"}


# --------------------------------------------------------------------------- #
# @tag handles
# --------------------------------------------------------------------------- #
def normalize_tag(raw) -> str:
    return (raw or "").strip().lstrip("@").lower()


def is_valid_tag(tag: str) -> bool:
    return bool(TAG_RE.match(tag)) and tag not in RESERVED_TAGS


def is_tag_available(tag: str, exclude_user_id=None) -> bool:
    qs = User.objects.filter(tag=tag)
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)
    return not qs.exists()


def suggest_tag(seed: str) -> str:
    """Build an available tag from a name/email seed, adding a suffix if taken."""
    base = re.sub(r"[^a-z0-9_]", "", (seed or "user").split("@")[0].lower()) or "user"
    base = base[:16]
    if len(base) < 3:
        base = (base + "user")[:6]
    if is_valid_tag(base) and is_tag_available(base):
        return base
    for _ in range(20):
        candidate = f"{base}{secrets.randbelow(9000) + 1000}"[:20]
        if is_tag_available(candidate):
            return candidate
    return f"user{secrets.token_hex(4)}"


def find_user_by_tag(tag: str):
    """Look up a CardPulse user by tag (realm-scoped)."""
    return User.objects.filter(tag=normalize_tag(tag), app=User.APP_CARDPULSE).first()


# --------------------------------------------------------------------------- #
# Request helpers
# --------------------------------------------------------------------------- #
def client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# --------------------------------------------------------------------------- #
# Ledger / profit / audit
# --------------------------------------------------------------------------- #
def record_ledger(user, direction, kind, amount, *, currency="NGN",
                  balance_after=None, reference="", description="", metadata=None):
    return LedgerEntry.objects.create(
        user=user, direction=direction, kind=kind, amount=amount, currency=currency,
        balance_after=balance_after, reference=reference, description=description,
        metadata=metadata or {},
    )


def record_profit(amount, *, user=None, source="trade", currency="NGN", reference="", metadata=None):
    return ProfitEntry.objects.create(
        user=user, source=source, amount=amount, currency=currency,
        reference=reference, metadata=metadata or {},
    )


def record_audit(action, *, user=None, detail="", ip_address=None, metadata=None):
    return AuditLog.objects.create(
        user=user, action=action, detail=detail, ip_address=ip_address, metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# Rate config (admin-only; never serialized to the client)
# --------------------------------------------------------------------------- #
def get_rate_config() -> RateConfig:
    return RateConfig.get_solo()
