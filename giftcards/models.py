"""
Giftcard instances + purchase orders.

A GiftCard is a real card we minted via Reloadly (or one traded back into
platform inventory). Its secret (number + PIN) is stored ENCRYPTED and only
ever decrypted when the owner explicitly reveals it — at which point it stops
being tradeable, so the same card can never be both spent and cashed out.
"""
from django.conf import settings
from django.db import models


class GiftCard(models.Model):
    STATUS_PROCESSING = "processing"   # mint placed, code not yet retrieved
    STATUS_OWNED = "owned"             # active, code in escrow, tradeable
    STATUS_REVEALED = "revealed"       # owner exposed the code -> NOT tradeable
    STATUS_TRADED = "traded"           # cashed out; card returned to inventory
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_OWNED, "Owned"),
        (STATUS_REVEALED, "Revealed"),
        (STATUS_TRADED, "Traded"),
        (STATUS_FAILED, "Failed"),
    ]

    SOURCE_MINTED = "minted"
    SOURCE_TRADED = "traded"
    SOURCE_CHOICES = [(SOURCE_MINTED, "Minted"), (SOURCE_TRADED, "Traded back")]

    # owner == NULL means the card sits in platform inventory (e.g. after a
    # trade-back) available for resale.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="giftcards",
    )

    product_id = models.IntegerField()
    product_name = models.CharField(max_length=160)
    brand = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=8, blank=True, default="")
    currency = models.CharField(max_length=8, default="USD")

    face_value = models.DecimalField(max_digits=14, decimal_places=2)        # card currency
    face_value_ngn = models.DecimalField(max_digits=14, decimal_places=2)    # what buyer paid (NGN)
    cost_ngn = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # our cost basis

    # Encrypted secrets — never returned except on explicit reveal.
    code_encrypted = models.TextField(blank=True, default="")
    pin_encrypted = models.TextField(blank=True, default="")

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default=SOURCE_MINTED)
    redeemable = models.BooleanField(default=True)

    reloadly_transaction_id = models.CharField(max_length=64, blank=True, default="")
    custom_identifier = models.CharField(max_length=80, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "redeemable"]),
            models.Index(fields=["product_id", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product_name} {self.face_value}{self.currency} ({self.status})"

    @property
    def has_code(self) -> bool:
        return bool(self.code_encrypted)


class GiftCardOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="giftcard_orders")
    product_id = models.IntegerField()
    product_name = models.CharField(max_length=160, blank=True, default="")
    face_value = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)        # paid to provider (sender ccy)
    amount_ngn = models.DecimalField(max_digits=14, decimal_places=2)        # debited from wallet
    quantity = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # Idempotency: a retried purchase with the same key never double-charges.
    idempotency_key = models.CharField(max_length=80, unique=True)
    reloadly_transaction_id = models.CharField(max_length=64, blank=True, default="")
    card = models.ForeignKey(GiftCard, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    error = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"]), models.Index(fields=["status"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.id} {self.product_name} [{self.status}]"
