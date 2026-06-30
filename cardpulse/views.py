import logging

from django.contrib.auth import authenticate, get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from users.services import get_or_create_wallet
from users.views import get_currency_from_country

from . import services
from .permissions import IsCardPulseUser
from .serializers import (
    CardPulseRegisterSerializer,
    CardPulseLoginSerializer,
    CardPulseUserSerializer,
    TagCheckSerializer,
    SetTagSerializer,
    SetTransactionPinSerializer,
    ChangeTransactionPinSerializer,
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

        services.record_audit(
            "cardpulse_register", user=user, ip_address=services.client_ip(request),
            detail=f"tag=@{user.tag}",
        )
        return auth_response(user, wallet, http_status=status.HTTP_201_CREATED)


class CardPulseLoginView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CardPulseLoginSerializer
    throttle_scope = "login"

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        user = authenticate(request, email=email, password=password)
        # Realm isolation: a SocialPulse account cannot log in through CardPulse.
        if not user or getattr(user, "app", None) != User.APP_CARDPULSE:
            return Response({"error": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        wallet = get_or_create_wallet(user, currency=get_currency_from_country(user.country))
        services.record_audit(
            "cardpulse_login", user=user, ip_address=services.client_ip(request),
        )
        return auth_response(user, wallet)


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
