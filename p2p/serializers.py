from decimal import Decimal

from rest_framework import serializers

from giftcards.serializers import GiftCardSerializer
from .models import Transfer


class SendCashSerializer(serializers.Serializer):
    tag = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1"))
    pin = serializers.CharField(write_only=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=140)


class SendGiftcardSerializer(serializers.Serializer):
    tag = serializers.CharField()
    card_id = serializers.IntegerField()
    pin = serializers.CharField(write_only=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=140)


class TransferSerializer(serializers.ModelSerializer):
    direction = serializers.SerializerMethodField()
    counterparty = serializers.SerializerMethodField()
    # amount_ngn / currency are shown from the VIEWER's side: the sender sees the
    # amount they sent (their currency); the recipient sees what they received
    # (their currency). Differs only on a cross-currency transfer.
    amount_ngn = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    card = GiftCardSerializer(read_only=True)

    class Meta:
        model = Transfer
        fields = (
            "id", "kind", "direction", "counterparty", "amount_ngn", "currency",
            "card", "note", "status", "created_at",
        )

    def _me(self):
        request = self.context.get("request")
        return request.user if request else None

    def get_direction(self, obj):
        me = self._me()
        return "out" if me and obj.sender_id == me.id else "in"

    def get_amount_ngn(self, obj):
        me = self._me()
        if me and obj.recipient_id == me.id:
            return obj.recv_amount if obj.recv_amount is not None else obj.amount_ngn
        return obj.amount_ngn

    def get_currency(self, obj):
        me = self._me()
        if me and obj.recipient_id == me.id:
            return obj.recv_currency or obj.currency
        return obj.currency

    def get_counterparty(self, obj):
        me = self._me()
        other = obj.recipient if (me and obj.sender_id == me.id) else obj.sender
        return {"tag": other.tag, "name": other.full_name}
