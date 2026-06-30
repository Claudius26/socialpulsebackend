from decimal import Decimal

from rest_framework import serializers

from .models import GiftCard, GiftCardOrder, GiftCardTrade


class GiftCardSerializer(serializers.ModelSerializer):
    """Safe card representation — NEVER includes the code/pin."""
    has_code = serializers.BooleanField(read_only=True)

    class Meta:
        model = GiftCard
        fields = (
            "id", "product_name", "brand", "country", "currency",
            "face_value", "face_value_ngn", "status", "redeemable",
            "has_code", "source", "created_at",
        )


class GiftCardOrderSerializer(serializers.ModelSerializer):
    card = GiftCardSerializer(read_only=True)

    class Meta:
        model = GiftCardOrder
        fields = (
            "id", "product_id", "product_name", "face_value", "currency",
            "amount_ngn", "status", "error", "card", "created_at",
        )


class PurchaseSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    face_value = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.5"))
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=80)


class RevealSerializer(serializers.Serializer):
    pin = serializers.CharField(write_only=True)


class TradeSerializer(serializers.Serializer):
    """User-facing trade result. Deliberately excludes value_ngn, payout_rate
    and profit_ngn — the trader only ever sees what THEY receive."""
    pin = serializers.CharField(write_only=True)


class TradeResultSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()

    class Meta:
        model = GiftCardTrade
        # NOTE: no value_ngn / payout_rate / profit_ngn — margin stays hidden.
        fields = ("id", "product", "face_value", "currency", "payout_ngn", "status", "created_at")

    def get_product(self, obj):
        return obj.card.product_name if obj.card_id else ""
