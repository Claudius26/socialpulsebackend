from django.db.models import Q
from rest_framework import generics
from rest_framework.response import Response

from cardpulse.permissions import IsCardPulseUser, IsVerifiedCardPulseUser
from cardpulse.services import client_ip

from . import services
from .models import Transfer
from .serializers import SendCashSerializer, SendGiftcardSerializer, TransferSerializer


class TagLookupView(generics.GenericAPIView):
    """Find a friend by @tag before sending. Returns name only — never email."""
    permission_classes = [IsCardPulseUser]

    def get(self, request):
        tag = request.query_params.get("tag", "")
        return Response(services.lookup(tag), status=200)


class SendCashView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = SendCashSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            transfer = services.send_cash(
                request.user, d["tag"], d["amount"], d["pin"],
                note=d.get("note", ""), ip=client_ip(request),
            )
        except services.P2PError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(TransferSerializer(transfer, context={"request": request}).data, status=201)


class SendGiftcardView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = SendGiftcardSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            transfer = services.send_giftcard(
                request.user, d["tag"], d["card_id"], d["pin"],
                note=d.get("note", ""), ip=client_ip(request),
            )
        except services.P2PError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(TransferSerializer(transfer, context={"request": request}).data, status=201)


class TransferHistoryView(generics.ListAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = TransferSerializer

    def get_queryset(self):
        u = self.request.user
        return Transfer.objects.filter(Q(sender=u) | Q(recipient=u)).select_related(
            "sender", "recipient", "card"
        )
