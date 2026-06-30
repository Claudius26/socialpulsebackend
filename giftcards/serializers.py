from decimal import Decimal

from rest_framework import serializers

from .models import GiftCard, GiftCardOrder


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
