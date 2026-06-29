import secrets
import hashlib

from django.db import models
from django.conf import settings


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def generate_api_key():
    """
    Create a new API key. Returns (full_key, prefix, key_hash).
    The full key is shown to the user exactly once; we only persist its hash.
    """
    raw = secrets.token_hex(24)
    full_key = f"sp_live_{raw}"
    prefix = full_key[:16]  # 'sp_live_' + 8 chars, safe to display
    return full_key, prefix, hash_key(full_key)


class ApiKey(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=100, default="Default")
    prefix = models.CharField(max_length=24, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.prefix}…"
