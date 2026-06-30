from django.contrib import admin

from .models import Withdrawal


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "bank_code", "account_number",
                    "status", "refunded", "reviewer", "created_at")
    list_filter = ("status", "refunded")
    search_fields = ("user__email", "user__tag", "account_number", "reference", "transfer_code")
    readonly_fields = ("reference", "idempotency_key", "recipient_code", "transfer_code",
                       "created_at", "updated_at")
