"""
CardPulse admin API (staff only) — track users, money, queues, rates, profit.

All endpoints require IsAdminUser. Read views are live (no caching) for an
accurate operational picture; the queue actions reuse the same vetted service
functions as the user flows.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum, Count, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from banking.models import Withdrawal
from banking import services as banking_services
from giftcards.models import GiftCard, GiftCardOrder, GiftCardTrade
from giftcards import services as giftcard_services
from p2p.models import Transfer
from users.models import Wallet

from .models import RateConfig, ProfitEntry, AuditLog, LedgerEntry

User = get_user_model()
CARDPULSE = "cardpulse"


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))["t"] or Decimal("0")


@api_view(["GET"])
@permission_classes([IsAdminUser])
def overview(request):
    users = User.objects.filter(app=CARDPULSE)
    wallets = Wallet.objects.filter(user__app=CARDPULSE)
    trades = GiftCardTrade.objects.all()
    withdrawals = Withdrawal.objects.all()
    inventory = GiftCard.objects.filter(owner__isnull=True, status=GiftCard.STATUS_TRADED)

    return Response({
        "users": users.count(),
        "wallet_liability": float(_sum(wallets, "balance")),
        "total_profit": float(_sum(ProfitEntry.objects.all(), "amount")),
        "giftcards": {
            "orders": GiftCardOrder.objects.count(),
            "orders_completed": GiftCardOrder.objects.filter(status="completed").count(),
            "minted_active": GiftCard.objects.filter(
                owner__isnull=False).exclude(status=GiftCard.STATUS_TRADED).count(),
            "inventory_count": inventory.count(),
            "inventory_value_ngn": float(_sum(inventory, "face_value_ngn")),
        },
        "trades": {
            "total": trades.count(),
            "completed": trades.filter(status="completed").count(),
            "pending_review": trades.filter(status="pending_review").count(),
            "payout_volume": float(_sum(trades.filter(status="completed"), "payout_ngn")),
            "profit_volume": float(_sum(trades.filter(status="completed"), "profit_ngn")),
        },
        "withdrawals": {
            "total": withdrawals.count(),
            "success": withdrawals.filter(status="success").count(),
            "processing": withdrawals.filter(status="processing").count(),
            "pending_review": withdrawals.filter(status="pending_review").count(),
            "failed": withdrawals.filter(status__in=["failed", "reversed"]).count(),
            "paid_out": float(_sum(withdrawals.filter(status="success"), "amount")),
        },
        "transfers": Transfer.objects.count(),
    }, status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def users_list(request):
    rows = (
        User.objects.filter(app=CARDPULSE)
        .select_related("wallet")
        .annotate(
            cards=Count("giftcards", distinct=True),
            trades=Count("giftcard_trades", distinct=True),
        )
        .order_by("-date_joined")[:500]
    )
    return Response([
        {
            "id": u.id, "email": u.email, "full_name": u.full_name, "tag": u.tag,
            "balance": float(getattr(getattr(u, "wallet", None), "balance", 0) or 0),
            "is_active": u.is_active,
            "cards": u.cards, "trades": u.trades,
            "date_joined": u.date_joined.isoformat() if u.date_joined else None,
        }
        for u in rows
    ], status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def inventory(request):
    cards = GiftCard.objects.filter(
        owner__isnull=True, status=GiftCard.STATUS_TRADED
    ).order_by("-created_at")[:500]
    return Response([
        {
            "id": c.id, "product_name": c.product_name, "brand": c.brand,
            "currency": c.currency, "face_value": float(c.face_value),
            "value_ngn": float(c.face_value_ngn), "created_at": c.created_at.isoformat(),
        }
        for c in cards
    ], status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def trades_queue(request):
    status_f = request.query_params.get("status", "pending_review")
    qs = GiftCardTrade.objects.select_related("user", "card").filter(status=status_f)[:500]
    return Response([
        {
            "id": t.id, "user_email": t.user.email, "user_tag": t.user.tag,
            "product": t.card.product_name if t.card_id else "",
            "face_value": float(t.face_value), "currency": t.currency,
            "value_ngn": float(t.value_ngn), "payout_ngn": float(t.payout_ngn),
            "profit_ngn": float(t.profit_ngn), "status": t.status,
            "created_at": t.created_at.isoformat(),
        }
        for t in qs
    ], status=200)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trade_approve(request, pk):
    try:
        giftcard_services.approve_trade(request.user, pk)
    except giftcard_services.GiftcardError as exc:
        return Response({"error": exc.message}, status=exc.status)
    return Response({"message": "Trade approved and paid."}, status=200)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trade_reject(request, pk):
    try:
        giftcard_services.reject_trade(request.user, pk, request.data.get("reason", ""))
    except giftcard_services.GiftcardError as exc:
        return Response({"error": exc.message}, status=exc.status)
    return Response({"message": "Trade rejected."}, status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def withdrawals_queue(request):
    status_f = request.query_params.get("status", "pending_review")
    qs = Withdrawal.objects.select_related("user").filter(status=status_f)[:500]
    return Response([
        {
            "id": w.id, "user_email": w.user.email, "user_tag": w.user.tag,
            "amount": float(w.amount), "bank_code": w.bank_code,
            "account_number": w.account_number, "account_name": w.account_name,
            "status": w.status, "created_at": w.created_at.isoformat(),
        }
        for w in qs
    ], status=200)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def withdrawal_approve(request, pk):
    try:
        banking_services.approve_withdrawal(request.user, pk)
    except banking_services.BankingError as exc:
        return Response({"error": exc.message}, status=exc.status)
    return Response({"message": "Withdrawal approved."}, status=200)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def withdrawal_reject(request, pk):
    try:
        banking_services.reject_withdrawal(request.user, pk, request.data.get("reason", ""))
    except banking_services.BankingError as exc:
        return Response({"error": exc.message}, status=exc.status)
    return Response({"message": "Withdrawal rejected and refunded."}, status=200)


@api_view(["GET", "PUT"])
@permission_classes([IsAdminUser])
def rates(request):
    cfg = RateConfig.get_solo()
    if request.method == "PUT":
        for field in ("trade_payout_rate", "buy_markup_rate", "manual_review_threshold"):
            if field in request.data:
                setattr(cfg, field, Decimal(str(request.data[field])))
        cfg.save()
    return Response({
        "trade_payout_rate": float(cfg.trade_payout_rate),
        "buy_markup_rate": float(cfg.buy_markup_rate),
        "manual_review_threshold": float(cfg.manual_review_threshold),
        "updated_at": cfg.updated_at.isoformat(),
    }, status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def profit(request):
    by_source = (
        ProfitEntry.objects.values("source")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    return Response({
        "total": float(_sum(ProfitEntry.objects.all(), "amount")),
        "by_source": [
            {"source": r["source"], "total": float(r["total"] or 0), "count": r["count"]}
            for r in by_source
        ],
    }, status=200)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def audit(request):
    rows = AuditLog.objects.select_related("user").all()[:300]
    return Response([
        {
            "id": a.id, "action": a.action,
            "user_email": a.user.email if a.user_id else None,
            "detail": a.detail, "ip": a.ip_address,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ], status=200)
