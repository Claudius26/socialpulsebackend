from rest_framework import serializers
from .models import VirtualNumber, ReceivedSMS

class ReceivedSMSSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceivedSMS
        fields = "__all__"

class VirtualNumberSerializer(serializers.ModelSerializer):
    messages = ReceivedSMSSerializer(many=True, read_only=True)

    class Meta:
        model = VirtualNumber
        fields = "__all__"
