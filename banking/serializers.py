from decimal import Decimal

from rest_framework import serializers

from .models import Withdrawal


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Withdrawal
        fields = ("id", "amount", "currency", "bank_code", "account_number",
                  "account_name", "status", "error", "created_at")


# The real minimum is enforced on the NGN equivalent in the service layer (a
# wallet may be in GHS/KES/etc. where 1000 of the unit isn't the right floor),
# so here we only require a positive amount.
class InitiateWithdrawalSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    bank_code = serializers.CharField(max_length=12)
    account_number = serializers.CharField(max_length=20)
    pin = serializers.CharField(write_only=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=80)


class ResolveAccountSerializer(serializers.Serializer):
    account_number = serializers.CharField(max_length=20)
    bank_code = serializers.CharField(max_length=12)


class DepositSerializer(serializers.Serializer):
    # Minimum enforced (in NGN equivalent) by the service layer.
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    callback_url = serializers.CharField(required=False, allow_blank=True)
