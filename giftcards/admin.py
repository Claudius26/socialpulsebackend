from django.contrib import admin

from .models import GiftCard, GiftCardOrder, GiftCardTrade, GiftCardSale


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    list_display = ("id", "product_name", "owner", "face_value", "currency",
                    "status", "source", "redeemable", "created_at")
    list_filter = ("status", "source", "redeemable", "currency")
    search_fields = ("product_name", "owner__email", "owner__tag", "reloadly_transaction_id")
    # Never expose the encrypted secrets in the admin UI.
    exclude = ("code_encrypted", "pin_encrypted")
    readonly_fields = ("reloadly_transaction_id", "custom_identifier", "created_at", "updated_at")


@admin.register(GiftCardOrder)
class GiftCardOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "product_name", "amount_ngn", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "user__tag", "idempotency_key", "reloadly_transaction_id")


@admin.register(GiftCardTrade)
class GiftCardTradeAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "payout_ngn", "profit_ngn", "value_ngn",
                    "status", "reviewer", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "user__tag")
    readonly_fields = ("created_at", "reviewed_at")


@admin.register(GiftCardSale)
class GiftCardSaleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "brand", "country", "face_value", "currency",
                    "status", "payout_ngn", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("user__email", "user__tag", "brand")
    # Don't surface the encrypted code or the (large) image blob in the list.
    exclude = ("code_encrypted",)
    readonly_fields = ("image_base64", "validation_ref", "created_at", "reviewed_at")
