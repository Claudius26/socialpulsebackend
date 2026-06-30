import logging

from django.contrib.auth import authenticate, get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from users.services import get_or_create_wallet
from users.views import get_currency_from_country

from django.utils import timezone

from . import services
from . import email_utils
from .models import EmailOTP
from .permissions import IsCardPulseUser
from .serializers import (
    CardPulseRegisterSerializer,
    CardPulseLoginSerializer,
    CardPulseUserSerializer,
    TagCheckSerializer,
    SetTagSerializer,
    SetTransactionPinSerializer,
    ChangeTransactionPinSerializer,
    VerifyEmailSerializer,
    ResendOTPSerializer,
    ChangePasswordSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def cardpulse_wallet_payload(wallet) -> dict:
    """CardPulse only uses the cash balance — keep the payload minimal."""
    if not wallet:
        return {"balance": 0.0, "currency": "NGN"}
    return {"balance": float(wallet.balance or 0), "currency": wallet.currency}


def issue_token(user) -> str:
    """Access token carrying the realm claim, so CardPulse and SocialPulse
    tokens are distinguishable downstream."""
    refresh = RefreshToken.for_user(user)
    refresh["realm"] = getattr(user, "app", "")
    return str(refresh.access_token)


def auth_response(user, wallet, http_status=status.HTTP_200_OK):
    return Response(
        {
            "user": {
                **CardPulseUserSerializer(user).data,
                "wallet": cardpulse_wallet_payload(wallet),
            },
            "token": issue_token(user),
        },
        status=http_status,
    )


class CardPulseRegisterView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CardPulseRegisterSerializer
    throttle_scope = "register"

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        currency = get_currency_from_country(user.country)
        wallet = get_or_create_wallet(user, currency=currency)

        # Send the email-verification OTP. Don't fail signup if SMTP hiccups —
        # the user can resend from the verification screen.
        email_utils.issue_and_send(user, EmailOTP.PURPOSE_VERIFY)

        services.record_audit(
            "cardpulse_register", user=user, ip_address=services.client_ip(request),
            detail=f"@{user.tag}",
        )
        return auth_response(user, wallet, http_status=status.HTTP_201_CREATED)


class CardPulseLoginView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CardPulseLoginSerializer
    throttle_scope = "login"

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from django.db.models import Q

        login = serializer.validated_data["login"].strip()
        password = serializer.validated_data["password"]

        # Resolve email OR username/@tag -> the account. CardPulse users by realm;
        # staff/admins are allowed in too (so one admin works across both apps).
        realm_or_staff = Q(app=User.APP_CARDPULSE) | Q(is_staff=True)
        if "@" in login:
            account = User.objects.filter(realm_or_staff, email__iexact=login).first()
        else:
            account = User.objects.filter(realm_or_staff).filter(
                Q(tag=services.normalize_tag(login)) | Q(username__iexact=login)
            ).first()

        user = authenticate(request, email=account.email, password=password) if account else None
        if not user or (getattr(user, "app", None) != User.APP_CARDPULSE and not user.is_staff):
            return Response({"error": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        wallet = get_or_create_wallet(user, currency=get_currency_from_country(user.country))
        services.record_audit("cardpulse_login", user=user, ip_address=services.client_ip(request))
        return auth_response(user, wallet)


class VerifyEmailView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = VerifyEmailSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if user.email_verified:
            return Response({"email_verified": True}, status=200)

        otp = EmailOTP.objects.filter(
            user=user, purpose=EmailOTP.PURPOSE_VERIFY, used=False
        ).order_by("-created_at").first()
        if not otp or otp.expires_at < timezone.now():
            return Response({"error": "Code expired. Request a new one."}, status=400)
        if otp.attempts >= 5:
            return Response({"error": "Too many attempts. Request a new code."}, status=429)

        if not otp.check_code(serializer.validated_data["code"]):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return Response({"error": "Incorrect code."}, status=400)

        otp.used = True
        otp.save(update_fields=["used"])
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        services.record_audit("cardpulse_email_verified", user=user)
        return Response({"email_verified": True}, status=200)


class ResendOTPView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = ResendOTPSerializer

    def post(self, request):
        user = request.user
        if user.email_verified:
            return Response({"email_verified": True}, status=200)
        # Cooldown: don't spam.
        last = EmailOTP.objects.filter(user=user, purpose=EmailOTP.PURPOSE_VERIFY).order_by("-created_at").first()
        if last and (timezone.now() - last.created_at).total_seconds() < email_utils.RESEND_COOLDOWN_SECONDS:
            return Response({"error": "Please wait a minute before requesting another code."}, status=429)
        email_utils.issue_and_send(user, EmailOTP.PURPOSE_VERIFY)
        return Response({"message": "A new code has been sent."}, status=200)


class ChangePasswordView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        services.record_audit("cardpulse_change_password", user=user, ip_address=services.client_ip(request))
        return Response({"message": "Password updated."}, status=200)


class CardPulseMeView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]

    def get(self, request):
        user = request.user
        wallet = get_or_create_wallet(user, currency=get_currency_from_country(user.country))
        return Response(
            {
                "user": {
                    **CardPulseUserSerializer(user).data,
                    "wallet": cardpulse_wallet_payload(wallet),
                }
            },
            status=status.HTTP_200_OK,
        )


class TagCheckView(generics.GenericAPIView):
    """Public so the registration screen can validate a tag before signup."""
    permission_classes = [permissions.AllowAny]
    serializer_class = TagCheckSerializer

    def get(self, request):
        tag = services.normalize_tag(request.query_params.get("tag"))
        if not services.is_valid_tag(tag):
            return Response(
                {"tag": tag, "available": False,
                 "reason": "Tag must be 3-20 chars: lowercase letters, numbers, underscore."},
                status=status.HTTP_200_OK,
            )
        available = services.is_tag_available(tag)
        body = {"tag": tag, "available": available}
        if not available:
            body["suggestions"] = [services.suggest_tag(tag) for _ in range(2)]
        return Response(body, status=status.HTTP_200_OK)


class SetTagView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = SetTagSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.tag = serializer.validated_data["tag"]
        user.save(update_fields=["tag"])
        services.record_audit("cardpulse_set_tag", user=user, detail=f"@{user.tag}")
        return Response({"tag": user.tag}, status=status.HTTP_200_OK)


class SetTransactionPinView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = SetTransactionPinSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_transaction_pin(serializer.validated_data["pin"])
        user.save(update_fields=["transaction_pin"])
        services.record_audit(
            "cardpulse_set_pin", user=user, ip_address=services.client_ip(request),
        )
        return Response({"has_transaction_pin": True}, status=status.HTTP_200_OK)


class ChangeTransactionPinView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = ChangeTransactionPinSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_transaction_pin(serializer.validated_data["new_pin"])
        user.save(update_fields=["transaction_pin"])
        services.record_audit(
            "cardpulse_change_pin", user=user, ip_address=services.client_ip(request),
        )
        return Response({"has_transaction_pin": True}, status=status.HTTP_200_OK)
