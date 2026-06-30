import os
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from virtualnumbers.models import VirtualNumber
from users.models import Wallet
from common.cache_utils import invalidate_user_wallet_caches

# A number is only auto-cancelled once it has waited its FULL window with no SMS.
# The window is measured from the purchase time (created_at) and is configured via
# VIRTUALNUMBER_AUTO_CANCEL_MINUTES (default 20). We hard-floor it at 15 minutes
# ONLY as a safety net: a freshly-ordered number can NEVER be swept even if the
# env var is missing, 0, or misconfigured (which would otherwise make the cutoff
# ~= now and cancel everything, including orders placed seconds ago). The floor
# only ever raises a too-small value up to 15 — it never lowers a configured 20.
MIN_WAIT_MINUTES = 15


def _wait_minutes() -> int:
    try:
        configured = int(os.getenv("VIRTUALNUMBER_AUTO_CANCEL_MINUTES", "20"))
    except (TypeError, ValueError):
        configured = 20
    return max(MIN_WAIT_MINUTES, configured)


class Command(BaseCommand):
    help = "Auto-cancel numbers that received no SMS after their wait window, releasing the held funds."

    def handle(self, *args, **options):
        wait_minutes = _wait_minutes()
        cutoff = timezone.now() - timezone.timedelta(minutes=wait_minutes)

        # Only numbers whose purchase time is at/older than the cutoff — i.e. they
        # have already counted down their full wait window without an SMS.
        qs = (
            VirtualNumber.objects
            .filter(status__in=["Pending", "Active"], charged=False, created_at__lte=cutoff)
            .filter(sms_received_at__isnull=True)
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

        self.stdout.write(self.style.SUCCESS(
            f"Auto-cancelled {count} number(s) older than {wait_minutes} min with no SMS."
        ))
