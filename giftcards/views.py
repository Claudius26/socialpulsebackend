import logging
import uuid

from rest_framework import generics
from rest_framework.response import Response

from cardpulse.permissions import IsCardPulseUser, IsVerifiedCardPulseUser
from cardpulse.services import client_ip
from common.providers import get_giftcard_provider, ProviderError
from common.cache_utils import get_or_set_cache

from . import services
from .models import GiftCard, GiftCardOrder, GiftCardTrade, GiftCardSale
from .serializers import (
    GiftCardSerializer, GiftCardOrderSerializer, PurchaseSerializer, RevealSerializer,
    TradeSerializer, TradeResultSerializer, SubmitSaleSerializer, SaleSerializer,
)

logger = logging.getLogger(__name__)


class GiftcardCatalogView(generics.GenericAPIView):
    """Browse the giftcard catalog. Read-only, CardPulse users only.

    Query params: country (ISO, e.g. US), search (brand/product name),
    page, size.
    """
    permission_classes = [IsCardPulseUser]

    def get(self, request):
        country = (request.query_params.get("country") or "").strip().upper() or None
        search = (request.query_params.get("search") or "").strip() or None
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            size = min(100, max(1, int(request.query_params.get("size", 50))))
        except (TypeError, ValueError):
            size = 50

        try:
            data = services.fetch_catalog(country=country, page=page, size=size, search=search)
        except ProviderError as exc:
            logger.warning("Giftcard provider unavailable: %s", exc)
            return Response(
                {"error": "This service is temporarily unavailable. Please try again later."},
                status=503,
            )
        return Response(data, status=200)


class GiftcardProductView(generics.GenericAPIView):
    """Single product detail with NGN-priced denominations."""
    permission_classes = [IsCardPulseUser]

    def get(self, request, product_id):
        try:
            raw = get_giftcard_provider().get_product(product_id)
        except ProviderError as exc:
            logger.warning("Giftcard provider unavailable: %s", exc)
            return Response(
                {"error": "This service is temporarily unavailable. Please try again later."},
                status=503,
            )
        if not isinstance(raw, dict) or not raw.get("productId"):
            return Response({"error": "Product not found"}, status=404)
        return Response(services.normalize_product(raw), status=200)


class GiftcardCountriesView(generics.GenericAPIView):
    """Countries that have giftcards available (for the catalog filter)."""
    permission_classes = [IsCardPulseUser]

    def get(self, request):
        def fetch():
            try:
                rows = get_giftcard_provider().list_countries()
            except ProviderError:
                return []
            return [
                {"iso": c.get("isoName"), "name": c.get("name"), "flag": c.get("flagUrl"),
                 "currency": c.get("currencyCode")}
                for c in rows if isinstance(c, dict)
            ]

        data = get_or_set_cache("cardpulse:giftcard:countries", fetch, timeout=3600)
        return Response({"countries": data}, status=200)


class PurchaseGiftcardView(generics.GenericAPIView):
    """Buy (mint) a giftcard, charged to the CardPulse cash wallet."""
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = PurchaseSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Idempotency: client key (body or header) or a generated one.
        key = (data.get("idempotency_key") or "").strip() \
            or request.headers.get("Idempotency-Key", "").strip() \
            or uuid.uuid4().hex

        try:
            order = services.purchase_giftcard(
                request.user, data["product_id"], data["face_value"],
                idempotency_key=key, ip=client_ip(request),
            )
        except services.GiftcardError as exc:
            return Response({"error": exc.message}, status=exc.status)

        body = GiftCardOrderSerializer(order).data
        http_status = 201 if order.status == GiftCardOrder.STATUS_COMPLETED else (
            402 if order.status == GiftCardOrder.STATUS_FAILED else 202
        )
        return Response(body, status=http_status)


class MyGiftcardsView(generics.ListAPIView):
    """The signed-in user's giftcards (codes never included here)."""
    permission_classes = [IsCardPulseUser]
    serializer_class = GiftCardSerializer

    def get_queryset(self):
        return GiftCard.objects.filter(owner=self.request.user)


class GiftcardDetailView(generics.RetrieveAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = GiftCardSerializer

    def get_queryset(self):
        return GiftCard.objects.filter(owner=self.request.user)


class RevealGiftcardView(generics.GenericAPIView):
    """Reveal a card's code (requires txn PIN). Makes the card non-tradeable."""
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = RevealSerializer

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = services.reveal_card(
                request.user, pk, serializer.validated_data["pin"], ip=client_ip(request)
            )
        except services.GiftcardError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(payload, status=200)


class TradeGiftcardView(generics.GenericAPIView):
    """Cash out a card. Returns ONLY the payout the trader receives."""
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = TradeSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request, pk):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            trade = services.trade_card(request.user, pk, s.validated_data["pin"],
                                        ip=client_ip(request))
        except services.GiftcardError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(TradeResultSerializer(trade).data, status=201)


class MyTradesView(generics.ListAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = TradeResultSerializer

    def get_queryset(self):
        return GiftCardTrade.objects.filter(user=self.request.user).select_related("card")


class SubmitSaleView(generics.GenericAPIView):
    """Sell a giftcard you already own — snap it + details, we validate & pay."""
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = SubmitSaleSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            sale = services.submit_sale(
                request.user, brand=d["brand"], country=d["country"],
                currency=d.get("currency", "USD"), face_value=d["face_value"],
                code=d.get("code", ""), image=d.get("image", ""), ip=client_ip(request),
            )
        except services.GiftcardError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(SaleSerializer(sale).data, status=201)


class MySalesView(generics.ListAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = SaleSerializer

    def get_queryset(self):
        return GiftCardSale.objects.filter(user=self.request.user)
