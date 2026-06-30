from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from . import services

User = get_user_model()


class CardPulseRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)
    tag = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("full_name", "email", "phone", "country", "tag", "password", "password2")

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "Passwords do not match"})
        tag = services.normalize_tag(attrs.get("tag"))
        if tag:
            if not services.is_valid_tag(tag):
                raise serializers.ValidationError(
                    {"tag": "Tag must be 3-20 chars: lowercase letters, numbers, underscore."}
                )
            if not services.is_tag_available(tag):
                raise serializers.ValidationError({"tag": "That tag is already taken."})
        attrs["tag"] = tag
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        password = validated_data.pop("password")
        tag = validated_data.pop("tag", "") or services.suggest_tag(validated_data.get("email", ""))

        user = User.objects.create(
            **validated_data,
            app=User.APP_CARDPULSE,
            tag=tag,
            username=validated_data.get("email"),
        )
        user.set_password(password)
        user.save()
        return user


class CardPulseLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class TagCheckSerializer(serializers.Serializer):
    tag = serializers.CharField()


class SetTagSerializer(serializers.Serializer):
    tag = serializers.CharField()

    def validate_tag(self, value):
        tag = services.normalize_tag(value)
        if not services.is_valid_tag(tag):
            raise serializers.ValidationError(
                "Tag must be 3-20 chars: lowercase letters, numbers, underscore."
            )
        user = self.context["request"].user
        if not services.is_tag_available(tag, exclude_user_id=user.id):
            raise serializers.ValidationError("That tag is already taken.")
        return tag


class SetTransactionPinSerializer(serializers.Serializer):
    """First-time PIN set — requires the account password to authorize."""
    password = serializers.CharField(write_only=True)
    pin = serializers.RegexField(r"^\d{4,6}$", write_only=True)
    confirm_pin = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["password"]):
            raise serializers.ValidationError({"password": "Incorrect password."})
        if attrs["pin"] != attrs["confirm_pin"]:
            raise serializers.ValidationError({"pin": "PINs do not match."})
        return attrs


class ChangeTransactionPinSerializer(serializers.Serializer):
    old_pin = serializers.CharField(write_only=True)
    new_pin = serializers.RegexField(r"^\d{4,6}$", write_only=True)
    confirm_pin = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_transaction_pin(attrs["old_pin"]):
            raise serializers.ValidationError({"old_pin": "Incorrect current PIN."})
        if attrs["new_pin"] != attrs["confirm_pin"]:
            raise serializers.ValidationError({"new_pin": "PINs do not match."})
        return attrs


class CardPulseUserSerializer(serializers.ModelSerializer):
    has_transaction_pin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "full_name", "email", "phone", "country", "tag", "has_transaction_pin")
