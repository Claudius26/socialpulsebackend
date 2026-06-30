from rest_framework import generics
from rest_framework.response import Response

from cardpulse.permissions import IsCardPulseUser, IsVerifiedCardPulseUser
from cardpulse.services import client_ip

from . import services
from .models import Withdrawal
from .serializers import (
    WithdrawalSerializer, InitiateWithdrawalSerializer, ResolveAccountSerializer, DepositSerializer,
)


class BankListView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]

    def get(self, request):
        return Response({"banks": services.list_banks()}, status=200)


class ResolveAccountView(generics.GenericAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = ResolveAccountSerializer

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            name = services.resolve_account(s.validated_data["account_number"],
                                            s.validated_data["bank_code"])
        except services.BankingError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response({"account_name": name}, status=200)


class InitiateWithdrawalView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = InitiateWithdrawalSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            wd = services.initiate_withdrawal(
                request.user, d["amount"], d["bank_code"], d["account_number"], d["pin"],
                idempotency_key=d.get("idempotency_key"), ip=client_ip(request),
            )
        except services.BankingError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(WithdrawalSerializer(wd).data, status=201)


class MyWithdrawalsView(generics.ListAPIView):
    permission_classes = [IsCardPulseUser]
    serializer_class = WithdrawalSerializer

    def get_queryset(self):
        return Withdrawal.objects.filter(user=self.request.user)


class DepositInitView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCardPulseUser]
    serializer_class = DepositSerializer
    throttle_scope = "cardpulse_money"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            payload = services.create_deposit(
                request.user, s.validated_data["amount"],
                callback_url=s.validated_data.get("callback_url") or None,
            )
        except services.BankingError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(payload, status=201)
