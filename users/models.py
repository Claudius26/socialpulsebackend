from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings

class User(AbstractUser):
    # Which product this account belongs to. SocialPulse (web) and CardPulse
    # (mobile) share ONE backend but are separate user bases — this realm flag
    # keeps them isolated and lets admin/queries scope per product.
    APP_SOCIALPULSE = "socialpulse"
    APP_CARDPULSE = "cardpulse"
    APP_CHOICES = [
        (APP_SOCIALPULSE, "SocialPulse"),
        (APP_CARDPULSE, "CardPulse"),
    ]

    full_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)

    app = models.CharField(max_length=20, choices=APP_CHOICES, default=APP_SOCIALPULSE, db_index=True)

    # CardPulse @handle — lets friends send cards/cash by tag (Cashapp-style).
    # Lowercase, unique. Null for legacy/web users who never set one.
    tag = models.CharField(max_length=30, unique=True, blank=True, null=True)

    # Hashed transaction PIN (Django password hashers) gating money actions:
    # trade, send, withdraw. Never stored or returned in plaintext.
    transaction_pin = models.CharField(max_length=128, blank=True, null=True)

    # CardPulse: must verify their email via OTP before using the app.
    email_verified = models.BooleanField(default=False)

    # Last authenticated activity — drives the admin online/offline indicator.
    last_seen = models.DateTimeField(null=True, blank=True)

    @property
    def is_online(self) -> bool:
        from django.utils import timezone
        from datetime import timedelta
        return bool(self.last_seen and self.last_seen >= timezone.now() - timedelta(minutes=5))

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='custom_user_groups',
        blank=True
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='custom_user_permissions',
        blank=True
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def set_transaction_pin(self, raw_pin):
        from django.contrib.auth.hashers import make_password
        self.transaction_pin = make_password(str(raw_pin))

    def check_transaction_pin(self, raw_pin) -> bool:
        from django.contrib.auth.hashers import check_password
        if not self.transaction_pin:
            return False
        return check_password(str(raw_pin), self.transaction_pin)

    @property
    def has_transaction_pin(self) -> bool:
        return bool(self.transaction_pin)

    def __str__(self):
        return self.email

class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    reserved_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # Separate credit pool spent via the public developer API (funded by
    # transferring from `balance`). Kept apart from the main wallet on purpose.
    api_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    api_reserved_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=5, default="NGN")

    def __str__(self):
        return f"{self.user.email} - {self.balance} {self.currency}"
