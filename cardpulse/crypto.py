"""
Symmetric encryption for sensitive giftcard secrets (card number + PIN).

Codes are encrypted at rest with Fernet (AES-128-CBC + HMAC). We decrypt ONLY
when the owner explicitly reveals a card — never in list views, never in logs.

Key resolution:
  * CARDPULSE_FERNET_KEY (a urlsafe-base64 32-byte key) — REQUIRED in prod.
  * Fallback for local/dev/test: derive a stable key from DJANGO_SECRET_KEY so
    the app still runs without extra config. Never rely on the fallback in
    production — rotating SECRET_KEY would make stored codes unreadable.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _derive_key_from_secret() -> bytes:
    from django.conf import settings
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    key = os.getenv("CARDPULSE_FERNET_KEY")
    if key:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    return Fernet(_derive_key_from_secret())


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return ""
    return _fernet().encrypt(str(plaintext).encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
