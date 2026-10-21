"""Начисления, импорт банковской выписки, распределение оплат, пени."""
import calendar
import csv
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Tenant
from leasing.models import Contract

from .models import ZERO, Invoice, Payment, PaymentAllocation

CENT = Decimal("0.01")


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _invoice_number(contract: Contract, period: date, kind: str) -> str:
    suffix = "О" if kind == Invoice.Kind.DEPOSIT else ""
    return f"{contract.number}/{period:%Y%m}{suffix}"


# --- Начисления --------------------------------------------------------------

def generate_invoices(period: date) -> list[Invoice]:
    """Создаёт счета за месяц по всем действующим договорам.

    Если договор действует не весь месяц, сумма считается пропорционально дням.
    Повторный запуск за тот же месяц ничего не дублирует.
    """
    start, end = month_start(period), month_end(period)
    days_in_month = (end - start).days + 1
    due_day = min(settings.INVOICE_DUE_DAY, days_in_month)
    created = []
    contracts = Contract.objects.filter(
        status__in=(Contract.Status.ACTIVE, Contract.Status.TERMINATED, Contract.Status.COMPLETED),
        tenant_signed_at__isnull=False,
        date_from__lte=end,
        date_to__gte=start,
    ).select_related("tenant")
    for contract in contracts:
        lease_days = (min(end, contract.date_to) - max(start, contract.date_from)).days + 1
        ratio = Decimal(lease_days) / Decimal(days_in_month)
        try:
            with transaction.atomic():
                invoice, was_created = Invoice.objects.get_or_create(
                    contract=contract, period=start, kind=Invoice.Kind.RENT,
                    defaults={
                        "number": _invoice_number(contract, start, Invoice.Kind.RENT),
                        "rent_amount": _money(contract.monthly_rent * ratio),
                        "service_amount": _money(contract.monthly_service * ratio),
                        "due_date": start.replace(day=due_day),
                    },
                )
        except IntegrityError:
            continue  # параллельный запуск уже создал счёт
        if was_created:
            created.append(invoice)
    for tenant_id in {i.contract.tenant_id for i in created}:
        apply_advances(Tenant.objects.get(pk=tenant_id))
    return created


def create_deposit_invoice(contract: Contract) -> Invoice:
    period = month_start(contract.date_from)
    invoice, _ = Invoice.objects.get_or_create(
        contract=contract, period=period, kind=Invoice.Kind.DEPOSIT,
        defaults={
            "number": _invoice_number(contract, period, Invoice.Kind.DEPOSIT),
            "rent_amount": contract.deposit_rub,
            "service_amount": ZERO,
            "due_date": min(timezone.localdate() + timedelta(days=5), contract.date_from),
        },
    )
    return invoice


# --- Пени --------------------------------------------------------------------

def penalty_for(invoice: Invoice, as_of: date) -> Decimal:
    """Пени = непогашенная основная сумма × ставка договора × дни просрочки."""
    if invoice.kind != Invoice.Kind.RENT or as_of <= invoice.due_date:
        return ZERO
    principal = invoice.rent_amount + invoice.service_amount
    unpaid_principal = principal - min(invoice.paid_amount, principal)
    if unpaid_principal <= 0:
        return invoice.penalty_amount  # долг погашен, начисленные пени фиксируются
    days = (as_of - invoice.due_date).days
    return _money(unpaid_principal * invoice.contract.penalty_pct / Decimal(100) * days)


def accrue_penalties(as_of: date | None = None) -> int:
    """Пересчитывает пени по просроченным счетам. Идемпотентна в пределах дня."""
    as_of = as_of or timezone.localdate()
    updated = 0
    overdue = Invoice.objects.filter(
        kind=Invoice.Kind.RENT, due_date__lt=as_of
    ).exclude(status=Invoice.Status.PAID).select_related("contract")
    for invoice in overdue:
        new_penalty = penalty_for(invoice, as_of)
        if new_penalty != invoice.penalty_amount:
            Invoice.objects.filter(pk=invoice.pk).update(penalty_amount=new_penalty)
            invoice.penalty_amount = new_penalty
            invoice.refresh_status()
            Invoice.objects.filter(pk=invoice.pk).update(status=invoice.status)
            updated += 1
    return updated


# --- Оплаты ------------------------------------------------------------------

@transaction.atomic
def allocate_payment(payment: Payment) -> Decimal:
    """Гасит долги арендатора в порядке срока оплаты. Возвращает нераспределённый остаток."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.tenant_id is None:
        return payment.amount
    remaining = payment.unallocated_amount
    if remaining <= 0:
        return ZERO
    invoices = (
        Invoice.objects.select_for_update()
        .filter(contract__tenant_id=payment.tenant_id)
        .exclude(status=Invoice.Status.PAID)
        .order_by("due_date", "id")
    )
    for invoice in invoices:
        if remaining <= 0:
            break
        part = min(remaining, invoice.debt)
        if part <= 0:
            continue
        allocation, created = PaymentAllocation.objects.get_or_create(
            payment=payment, invoice=invoice, defaults={"amount": part}
        )
        if not created:
            allocation.amount = F("amount") + part
            allocation.save(update_fields=["amount"])
        invoice.paid_amount += part
        invoice.refresh_status()
        invoice.save(update_fields=["paid_amount", "status"])
        remaining -= part
    return remaining


def apply_advances(tenant: Tenant) -> None:
    """Направляет ранее нераспределённые платежи арендатора на новые счета."""
    for payment in Payment.objects.filter(tenant=tenant).prefetch_related("allocations"):
        if payment.unallocated_amount > 0:
            allocate_payment(payment)


@dataclass
class ImportReport:
    matched: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def total_rows(self):
        return len(self.matched) + len(self.unmatched) + len(self.duplicates) + len(self.errors)


def _parse_amount(raw: str) -> Decimal:
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".")
    value = Decimal(cleaned)
    if value <= 0:
        raise InvalidOperation
    return _money(value)


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    if "." in raw:
        d, m, y = raw.split(".")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(raw)


def import_statement(content: str) -> ImportReport:
    """Загружает выписку CSV: дата;ИНН;сумма;номер документа;назначение.

    Первая строка — заголовок. Разделитель — точка с запятой или запятая.
    Платёж привязывается к арендатору по ИНН плательщика и сразу распределяется
    по его неоплаченным счетам. Платежи с неизвестным ИНН сохраняются для
    ручного разбора.
    """
    report = ImportReport()
    content = content.lstrip("﻿")
    sample = content[:2048]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return report
    tenants = {t.inn: t for t in Tenant.objects.all()}
    for line_no, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            raw_date, inn, raw_amount, doc_no = (c.strip() for c in row[:4])
            purpose = row[4].strip() if len(row) > 4 else ""
            paid_at = _parse_date(raw_date)
            amount = _parse_amount(raw_amount)
            if not inn.isdigit() or not doc_no:
                raise ValueError
        except (ValueError, InvalidOperation, IndexError):
            report.errors.append({"line": line_no, "row": delimiter.join(row)})
            continue
        tenant = tenants.get(inn)
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    tenant=tenant, payer_inn=inn, amount=amount, paid_at=paid_at,
                    bank_doc_no=doc_no, purpose=purpose[:255], source=Payment.Source.IMPORT,
                )
        except IntegrityError:
            report.duplicates.append({"line": line_no, "doc_no": doc_no})
            continue
        if tenant is None:
            report.unmatched.append({"line": line_no, "payment": payment})
            continue
        left = allocate_payment(payment)
        report.matched.append({"line": line_no, "payment": payment, "advance": left})
    return report


@transaction.atomic
def assign_payment(payment: Payment, tenant: Tenant) -> Decimal:
    """Ручная привязка нераспознанного платежа к арендатору."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.tenant_id is not None:
        raise ValueError("Платёж уже привязан к арендатору.")
    payment.tenant = tenant
    payment.save(update_fields=["tenant"])
    return allocate_payment(payment)
