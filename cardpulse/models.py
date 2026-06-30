"""
CardPulse financial-core models.

These are the cross-cutting pieces every money feature (giftcards, p2p,
withdrawals) builds on:

  * RateConfig  — the SINGLE, admin-only source of truth for margins/pricing.
                  Never exposed to the client, so you can change your cut or
                  raise prices anytime and users only ever see final amounts.
  * LedgerEntry — an append-only record of every money movement (audit trail
                  + reconciliation). One row per credit/debit.
  * ProfitEntry — records your platform margin per transaction (your revenue).
  * AuditLog    — who did what, when, from where, for sensitive actions.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class RateConfig(models.Model):
    """Singleton (one active row) holding all configurable pricing levers.

    trade_payout_rate = the fraction of a card's value the TRADER receives.
    The platform keeps (1 - trade_payout_rate). Default 0.90 → trader gets 90%,
    you keep 10%. This value is admin-only and never returned to the app.
    """
    trade_payout_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.9000"))
    # Markup added when a user BUYS a giftcard to gift (small service fee).
    buy_markup_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))
    # Trades/withdrawals at or above this (NGN) route to manual admin review
    # instead of auto-paying. 0 = auto-approve everything.
    manual_review_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rate configuration"

    def __str__(self):
        return f"payout={self.trade_payout_rate} markup={self.buy_markup_rate}"

    @classmethod
    def get_solo(cls) -> "RateConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LedgerEntry(models.Model):
    """Append-only money-movement record. Never updated or deleted."""

    CREDIT = "credit"
    DEBIT = "debit"
    DIRECTION_CHOICES = [(CREDIT, "Credit"), (DEBIT, "Debit")]

    # Coarse category for filtering/reporting.
    KIND_CHOICES = [
        ("deposit", "Deposit"),
        ("giftcard_purchase", "Giftcard purchase"),
        ("trade_payout", "Trade payout"),
        ("transfer_in", "Transfer in"),
        ("transfer_out", "Transfer out"),
        ("withdrawal", "Withdrawal"),
        ("reversal", "Reversal"),
        ("adjustment", "Adjustment"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger_entries")
    direction = models.CharField(max_length=6, choices=DIRECTION_CHOICES)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=5, default="NGN")
    balance_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reference = models.CharField(max_length=120, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.direction} {self.amount} {self.currency} ({self.kind})"


class ProfitEntry(models.Model):
    """Your platform margin per transaction — the revenue ledger."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="profit_entries",
    )
    source = models.CharField(max_length=32, default="trade")  # trade / buy_markup / ...
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=5, default="NGN")
    reference = models.CharField(max_length=120, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["source", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"profit {self.amount} {self.currency} ({self.source})"


class EmailOTP(models.Model):
    """Short-lived, hashed one-time codes for email verification / resets."""
    PURPOSE_VERIFY = "verify"
    PURPOSE_RESET = "reset"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otps")
    purpose = models.CharField(max_length=16, default=PURPOSE_VERIFY)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "-created_at"])]
        ordering = ["-created_at"]

    def set_code(self, raw):
        from django.contrib.auth.hashers import make_password
        self.code_hash = make_password(str(raw))

    def check_code(self, raw) -> bool:
        from django.contrib.auth.hashers import check_password
        return check_password(str(raw), self.code_hash)


class AuditLog(models.Model):
    """Who did what, when, from where — for sensitive actions."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64)
    detail = models.CharField(max_length=255, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"]), models.Index(fields=["action", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.user_id} @ {self.created_at:%Y-%m-%d %H:%M}"
