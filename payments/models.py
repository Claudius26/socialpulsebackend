from django.db import models
from django.conf import settings
import uuid

class Deposit(models.Model):
    STATUS = [
        ("pending", "pending"),
        ("paid", "paid"),
        ("failed", "failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deposits")
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    method = models.CharField(max_length=20, default="paystack")
    provider_payload = models.JSONField(null=True, blank=True)
    provider_reference = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} - {self.amount} ({self.status})"
