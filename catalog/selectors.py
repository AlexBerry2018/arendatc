"""Запросы к каталогу: фильтрация и проверка доступности помещений."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db.models import Exists, OuterRef, QuerySet

from .models import Premises


def available_between(qs: QuerySet, d_from: date, d_to: date) -> QuerySet:
    """Помещения без активных броней и действующих договоров, пересекающих период."""
    from leasing.models import Booking, Contract

    bookings = Booking.objects.filter(
        premises=OuterRef("pk"), is_active=True, lease_from__lte=d_to, lease_to__gte=d_from
    )
    contracts = Contract.objects.filter(
        premises=OuterRef("pk"),
        status__in=Contract.BLOCKING_STATUSES,
        date_from__lte=d_to,
        date_to__gte=d_from,
    )
    return qs.exclude(status=Premises.Status.REPAIR).filter(~Exists(bookings), ~Exists(contracts))


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", ".")) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def filter_premises(params) -> tuple[QuerySet, dict]:
    """Применяет фильтры каталога. Возвращает queryset и очищенные значения фильтров."""
    qs = Premises.objects.select_related("floor", "type")
    cleaned = {
        "floor": params.get("floor") or "",
        "type": params.get("type") or "",
        "area_min": _decimal(params.get("area_min")),
        "area_max": _decimal(params.get("area_max")),
        "price_max": _decimal(params.get("price_max")),
        "period_from": _date(params.get("period_from")),
        "period_to": _date(params.get("period_to")),
        "only_free": params.get("only_free") in ("1", "on", "true"),
    }
    if cleaned["floor"]:
        qs = qs.filter(floor__number=cleaned["floor"])
    if cleaned["type"]:
        qs = qs.filter(type_id=cleaned["type"])
    if cleaned["area_min"] is not None:
        qs = qs.filter(area_m2__gte=cleaned["area_min"])
    if cleaned["area_max"] is not None:
        qs = qs.filter(area_m2__lte=cleaned["area_max"])
    if cleaned["price_max"] is not None:
        qs = qs.filter(rate_rub_m2__lte=cleaned["price_max"])
    if cleaned["period_from"] and cleaned["period_to"] and cleaned["period_to"] > cleaned["period_from"]:
        qs = available_between(qs, cleaned["period_from"], cleaned["period_to"])
    elif cleaned["only_free"]:
        qs = qs.filter(status=Premises.Status.FREE)
    return qs, cleaned
