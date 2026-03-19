import os
import requests

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from virtualnumbers.models import VirtualNumber

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
                r = requests.post(
                    ZAPOTP_CANCEL_URL,
                    headers=ZAPOTP_HEADERS,
                    json={"order_id": str(vn.activation_id)},
                    timeout=20,
                )
                try:
                    provider_resp = r.json()
                except Exception:
                    provider_resp = {"raw": r.text}

                if isinstance(provider_resp, dict) and provider_resp.get("status") != "success":
                    continue

                with transaction.atomic():
                    vn.status = "Cancelled"

                    vn.cancelled_at = timezone.now()
                    vn.save(update_fields=["status", "cancelled_at"])

                count += 1

            except Exception:
                continue

        self.stdout.write(self.style.SUCCESS(f"Auto-cancelled: {count}"))
