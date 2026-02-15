from rest_framework import serializers
from .models import BoostRequest

class BoostRequestSerializer(serializers.ModelSerializer):
    order_id = serializers.SerializerMethodField()

    class Meta:
        model = BoostRequest
        fields = "__all__"
        read_only_fields = [
            "user",
            "order",          
            "status",
            "created_at",
            "amount",
            "delivery_time",
            "smm_order_id",
            "smm_charge",
            "smm_start_count",
            "smm_remains",
            "smm_currency",
            "error_message",
        ]

    def get_order_id(self, obj):
        return str(obj.order.id) if obj.order else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["delivery_time"] = instance.delivery_time
        return data
