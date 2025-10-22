from rest_framework import serializers
from .models import BoostRequest

class BoostRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoostRequest
        fields = "__all__"
        read_only_fields = ["user", "status", "created_at", "amount", "delivery_time", "smm_charge", "smm_order_id", "smm_start_count", "smm_remains", "smm_currency"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["delivery_time"] = instance.delivery_time 
        return data
