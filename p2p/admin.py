from django.contrib import admin

from .models import Transfer


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "sender", "recipient", "amount_ngn", "card", "status", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("sender__email", "recipient__email", "sender__tag", "recipient__tag")
    readonly_fields = [f.name for f in Transfer._meta.fields]
