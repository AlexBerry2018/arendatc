from decimal import Decimal

from django import template

register = template.Library()

STATUS_BADGES = {
    # помещения
    "free": "success", "booked": "warning", "leased": "secondary", "repair": "danger",
    # заявки
    "pending": "warning", "approved": "info", "rejected": "danger", "expired": "dark", "contracted": "success",
    # договоры
    "draft": "secondary", "approval": "warning", "awaiting_signature": "info", "active": "success",
    "terminated": "danger", "completed": "dark", "cancelled": "dark",
    # счета
    "issued": "warning", "partially_paid": "info", "paid": "success",
}


@register.filter
def badge(status):
    return STATUS_BADGES.get(status, "secondary")


@register.filter
def rub(value):
    """12345.6 → «12 345,60 ₽»."""
    if value is None or value == "":
        return "—"
    value = Decimal(value).quantize(Decimal("0.01"))
    sign = "−" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    nbsp = " "
    return sign + nbsp.join(groups) + "," + frac + nbsp + "₽"


@register.filter
def rub0(value):
    """Сумма без копеек для крупных показателей: «2 171 635 ₽»."""
    if value is None or value == "":
        return "—"
    return rub(Decimal(value).quantize(Decimal("1"))).rsplit(",", 1)[0] + "\u00a0₽"


@register.filter
def num(value):
    if value is None:
        return "—"
    return f"{value}".replace(".", ",")


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs):
    """Текущие GET-параметры с заменой указанных ключей."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter
def add_money(value, other):
    return Decimal(value or 0) + Decimal(other or 0)
