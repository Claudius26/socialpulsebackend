"""
Peer-to-peer transfers — send cash or a giftcard to a friend by @tag.

Transfers are internal and atomic, so a row here is always a completed,
balanced movement (with matching LedgerEntry rows on both sides for cash).
"""
from django.conf import settings
from django.db import models


class Transfer(models.Model):
    KIND_CASH = "cash"
    KIND_GIFTCARD = "giftcard"
    KIND_CHOICES = [(KIND_CASH, "Cash"), (KIND_GIFTCARD, "Giftcard")]

    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_COMPLETED, "Completed"), (STATUS_FAILED, "Failed")]

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_transfers"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_transfers"
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)

    # cash transfers
    amount_ngn = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=5, default="NGN")

    # giftcard transfers
    card = models.ForeignKey(
        "giftcards.GiftCard", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers",
    )

    note = models.CharField(max_length=140, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["sender", "-created_at"]),
            models.Index(fields=["recipient", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.sender_id}->{self.recipient_id} ({self.status})"
