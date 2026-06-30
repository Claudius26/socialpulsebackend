from django.contrib import admin

from .models import RateConfig, LedgerEntry, ProfitEntry, AuditLog


@admin.register(RateConfig)
class RateConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "trade_payout_rate", "buy_markup_rate", "manual_review_threshold", "updated_at")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "direction", "kind", "amount", "currency", "balance_after", "created_at")
    list_filter = ("direction", "kind", "currency")
    search_fields = ("user__email", "reference", "description")
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]


@admin.register(ProfitEntry)
class ProfitEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "source", "amount", "currency", "created_at")
    list_filter = ("source", "currency")
    search_fields = ("user__email", "reference")
    readonly_fields = [f.name for f in ProfitEntry._meta.fields]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "action", "detail", "ip_address", "created_at")
    list_filter = ("action",)
    search_fields = ("user__email", "action", "detail")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
