import logging

from rest_framework import generics
from rest_framework.response import Response

from cardpulse.permissions import IsCardPulseUser
from common.providers import get_giftcard_provider, ProviderError
from common.cache_utils import get_or_set_cache

from . import services

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
            return Response({"error": f"Giftcard provider unavailable: {exc}"}, status=502)
        return Response(data, status=200)


class GiftcardProductView(generics.GenericAPIView):
    """Single product detail with NGN-priced denominations."""
    permission_classes = [IsCardPulseUser]

    def get(self, request, product_id):
        try:
            raw = get_giftcard_provider().get_product(product_id)
        except ProviderError as exc:
            return Response({"error": f"Giftcard provider unavailable: {exc}"}, status=502)
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
