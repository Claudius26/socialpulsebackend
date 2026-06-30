"""
Bank withdrawals — cash out the wallet to a real Nigerian bank via Paystack.

Funds are debited (held) the moment a withdrawal is created; the money only
truly leaves on transfer.success. On failure/reversal we refund automatically.
"""
from django.conf import settings
from django.db import models


class Withdrawal(models.Model):
    STATUS_PENDING_REVIEW = "pending_review"   # above threshold; awaiting admin
    STATUS_PROCESSING = "processing"           # sent to Paystack, awaiting result
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_PENDING_REVIEW, "Pending review"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REVERSED, "Reversed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="withdrawals")
    amount = models.DecimalField(max_digits=14, decimal_places=2)       # debited from wallet, in `currency`
    currency = models.CharField(max_length=5, default="NGN")
    # The NGN actually transferred via Paystack (Paystack settles in NGN). For
    # NGN wallets this equals `amount`; for others it's the converted payout.
    amount_ngn = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    bank_code = models.CharField(max_length=12)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=120, blank=True, default="")

    recipient_code = models.CharField(max_length=64, blank=True, default="")
    transfer_code = models.CharField(max_length=64, blank=True, default="")
    reference = models.CharField(max_length=80, unique=True)
    idempotency_key = models.CharField(max_length=80, unique=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_withdrawals",
    )
    error = models.CharField(max_length=255, blank=True, default="")
    refunded = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Withdrawal {self.id} {self.amount} [{self.status}]"
