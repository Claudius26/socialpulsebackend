from rest_framework import serializers
from .models import VirtualNumber, ReceivedSMS

class ReceivedSMSSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceivedSMS
        fields = "__all__"
        read_only_fields = ["id", "received_at"]

class VirtualNumberSerializer(serializers.ModelSerializer):
    messages = ReceivedSMSSerializer(many=True, read_only=True)

    class Meta:
        model = VirtualNumber
        fields = "__all__"
        read_only_fields = [
            "id", "user", "phone_number", "activation_id",
            "cost", "status", "created_at", "messages"
        ]
