from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from . import services

User = get_user_model()


class CardPulseRegisterSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=60)
    last_name = serializers.CharField(max_length=60)
    email = serializers.EmailField()
    country = serializers.CharField(max_length=60, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "Passwords do not match"})
        return attrs

    def create(self, validated_data):
        first = validated_data["first_name"].strip()
        last = validated_data["last_name"].strip()
        email = validated_data["email"]
        # Auto-generate a unique username (@tag) from the name; user can change it later.
        tag = services.suggest_tag(f"{first}{last}" or email)

        user = User.objects.create(
            app=User.APP_CARDPULSE,
            email=email,
            username=email,
            first_name=first,
            last_name=last,
            full_name=f"{first} {last}".strip(),
            country=(validated_data.get("country") or "").strip() or None,
            tag=tag,
            email_verified=False,
        )
        user.set_password(validated_data["password"])
        user.save()
        return user


class CardPulseLoginSerializer(serializers.Serializer):
    # Accept a username (@tag) OR an email in the same field.
    login = serializers.CharField()
    password = serializers.CharField(write_only=True)


class VerifyEmailSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\d{6}$")


class ResendOTPSerializer(serializers.Serializer):
    pass


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
    username = serializers.CharField(source="tag", read_only=True)

    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "full_name", "email", "phone",
                  "country", "tag", "username", "email_verified", "has_transaction_pin")


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value
