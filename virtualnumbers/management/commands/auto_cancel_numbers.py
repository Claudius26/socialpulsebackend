import os
import requests
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from virtualnumbers.models import VirtualNumber
from users.models import Wallet
from common.cache_utils import invalidate_user_wallet_caches
from common.providers import get_otp_provider, ProviderError

ZAPOTP_API_KEY = os.getenv("ZAPOTP_API_KEY")
ZAPOTP_HEADERS = {
    "Authorization": f"Bearer {ZAPOTP_API_KEY}",
    "Content-Type": "application/json",
}
ZAPOTP_CANCEL_URL = "https://www.zapotp.com/account/smspool/cancel_order.php"

AUTO_CANCEL_MINUTES = int(os.getenv("VIRTUALNUMBER_AUTO_CANCEL_MINUTES", "20"))


class Command(BaseCommand):
    help = "Auto-cancel ZapOTP orders that have no SMS after X minutes."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(minutes=AUTO_CANCEL_MINUTES)

        qs = (
            VirtualNumber.objects
            .filter(status__in=["Pending", "Active"], charged=False, created_at__lte=cutoff)
            .filter(messages__isnull=True)
        )

        count = 0
        for vn in qs.iterator():
            try:
                provider_resp = get_otp_provider().cancel(vn.activation_id)

                if isinstance(provider_resp, dict) and provider_resp.get("status") != "success":
                    continue

                with transaction.atomic():
                    # Release the held funds back to the user (mirrors
                    # CancelNumberView) — previously this was never done, so each
                    # auto-cancel permanently leaked the reservation.
                    wallet = Wallet.objects.select_for_update().get(user=vn.user)
                    wallet.reserved_balance = max(
                        Decimal("0"), wallet.reserved_balance - vn.cost
                    )
                    wallet.save(update_fields=["reserved_balance"])

                    vn.status = "Cancelled"
                    vn.cancelled_at = timezone.now()
                    vn.save(update_fields=["status", "cancelled_at"])

                invalidate_user_wallet_caches(vn.user_id)
                count += 1

            except Exception:
                continue

        self.stdout.write(self.style.SUCCESS(f"Auto-cancelled: {count}"))
