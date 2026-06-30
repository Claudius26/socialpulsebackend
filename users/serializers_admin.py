from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

class AdminLoginSerializer(serializers.Serializer):
    username = serializers.CharField()  # accepts username OR email
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        ident = (attrs.get("username") or "").strip()
        password = attrs.get("password")

        user = User.objects.filter(
            Q(username__iexact=ident) | Q(email__iexact=ident)
        ).first()
        if not user or not user.check_password(password):
            raise serializers.ValidationError("Invalid credentials")

        if not user.is_staff:
            raise serializers.ValidationError("Not authorized as admin")

        refresh = RefreshToken.for_user(user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "username": user.username,
        }