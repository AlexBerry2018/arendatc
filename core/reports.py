"""Отчёты для руководителя: заполняемость, доходность, задолженность."""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from catalog.models import Floor, Premises
from finance.models import ZERO, Invoice, PaymentAllocation
from finance.services import month_end, month_start
from leasing.models import Application, Contract

MONEY = DecimalField(max_digits=14, decimal_places=2)


def occupancy_by_floor():
    rows = []
    for floor in Floor.objects.order_by("number"):
        agg = Premises.objects.filter(floor=floor).aggregate(
            total=Coalesce(Sum("area_m2"), ZERO, output_field=MONEY),
            leased=Coalesce(Sum("area_m2", filter=Q(status=Premises.Status.LEASED)), ZERO, output_field=MONEY),
            booked=Coalesce(Sum("area_m2", filter=Q(status=Premises.Status.BOOKED)), ZERO, output_field=MONEY),
        )
        pct = (agg["leased"] / agg["total"] * 100).quantize(Decimal("0.1")) if agg["total"] else Decimal("0")
        rows.append({"floor": floor, **agg, "pct": pct})
    total = sum((r["total"] for r in rows), ZERO)
    leased = sum((r["leased"] for r in rows), ZERO)
    overall = (leased / total * 100).quantize(Decimal("0.1")) if total else Decimal("0")
    return rows, {"total": total, "leased": leased, "pct": overall}


def revenue_for_month(period: date):
    start, end = month_start(period), month_end(period)
    invoiced = Invoice.objects.filter(kind=Invoice.Kind.RENT, period=start).aggregate(
        s=Coalesce(Sum(F("rent_amount") + F("service_amount") + F("penalty_amount"), output_field=MONEY), ZERO)
    )["s"]
    collected = PaymentAllocation.objects.filter(
        payment__paid_at__gte=start, payment__paid_at__lte=end
    ).aggregate(s=Coalesce(Sum("amount"), ZERO, output_field=MONEY))["s"]
    return {"period": start, "invoiced": invoiced, "collected": collected}


def debtors(as_of: date | None = None, limit: int = 10):
    as_of = as_of or timezone.localdate()
    debt_expr = ExpressionWrapper(
        F("rent_amount") + F("service_amount") + F("penalty_amount") - F("paid_amount"), output_field=MONEY
    )
    rows = (
        Invoice.objects.filter(due_date__lt=as_of)
        .exclude(status=Invoice.Status.PAID)
        .values("contract__tenant__id", "contract__tenant__legal_name", "contract__tenant__inn")
        .annotate(debt=Sum(debt_expr))
        .filter(debt__gt=0)
        .order_by("-debt")[:limit]
    )
    return [
        {"tenant_id": r["contract__tenant__id"], "name": r["contract__tenant__legal_name"],
         "inn": r["contract__tenant__inn"], "debt": r["debt"]}
        for r in rows
    ]


def expiring_contracts(days: int = 60, today: date | None = None):
    today = today or timezone.localdate()
    return Contract.objects.filter(
        status=Contract.Status.ACTIVE, date_to__gte=today, date_to__lte=today + timedelta(days=days)
    ).select_related("tenant", "premises").order_by("date_to")


def pipeline_counts():
    return {
        "pending_applications": Application.objects.filter(status=Application.Status.PENDING).count(),
        "contracts_in_approval": Contract.objects.filter(status=Contract.Status.APPROVAL).count(),
        "awaiting_signature": Contract.objects.filter(status=Contract.Status.AWAITING_SIGNATURE).count(),
        "active_contracts": Contract.objects.filter(status=Contract.Status.ACTIVE).count(),
    }
