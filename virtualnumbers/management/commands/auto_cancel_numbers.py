import os
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from virtualnumbers.models import VirtualNumber
from users.models import Wallet
from common.cache_utils import invalidate_user_wallet_caches

AUTO_CANCEL_MINUTES = int(os.getenv("VIRTUALNUMBER_AUTO_CANCEL_MINUTES", "20"))


class Command(BaseCommand):
    help = "Auto-cancel numbers that received no SMS after X minutes, releasing the held funds."

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
                # ZapOTP has no cancel endpoint; the number expires on their side.
                # We just release the user's hold (wallet or API credit) and mark it.
                with transaction.atomic():
                    wallet = Wallet.objects.select_for_update().get(user=vn.user)
                    if vn.funding_source == "api":
                        wallet.api_reserved_balance = max(
                            Decimal("0"), wallet.api_reserved_balance - vn.cost
                        )
                        wallet.save(update_fields=["api_reserved_balance"])
                    else:
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
