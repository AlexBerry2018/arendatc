"""Бизнес-логика аренды: заявка → бронь → договор → подписание.

Все переходы статусов идут только через функции этого модуля, чтобы веб-страницы
и REST API вели себя одинаково. Каждая функция выполняется в транзакции и
блокирует строку помещения (SELECT ... FOR UPDATE), поэтому две параллельные
заявки на одно помещение обрабатываются по очереди. Ограничения EXCLUDE в
PostgreSQL — второй, независимый рубеж защиты от двойной сдачи.
"""
import hashlib
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import Tenant, User
from catalog.models import Premises

from .models import Application, Booking, Contract, ContractVersion


class LeasingError(Exception):
    """Нарушение бизнес-правила; текст показывается пользователю."""


class PremisesUnavailable(LeasingError):
    """Помещение занято на запрошенный период."""


# --- Вспомогательные функции -------------------------------------------------

def add_business_days(start: datetime, days: int) -> datetime:
    """Добавляет рабочие дни (пн–пт) к моменту времени. Праздники не учитываются."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _lock_premises(premises_id: int) -> Premises:
    return Premises.objects.select_for_update().get(pk=premises_id)


def _has_blocking_contract(premises: Premises, d_from: date, d_to: date, exclude_id=None) -> bool:
    qs = Contract.objects.filter(
        premises=premises,
        status__in=Contract.BLOCKING_STATUSES,
        date_from__lte=d_to,
        date_to__gte=d_from,
    )
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def refresh_premises_status(premises: Premises, today: date | None = None) -> Premises:
    """Пересчитывает статус помещения по действующим договорам и броням."""
    today = today or timezone.localdate()
    if premises.status == Premises.Status.REPAIR:
        return premises
    if Contract.objects.filter(
        premises=premises, status=Contract.Status.ACTIVE, date_from__lte=today, date_to__gte=today
    ).exists():
        new_status = Premises.Status.LEASED
    elif Booking.objects.filter(premises=premises, is_active=True).exists() or Contract.objects.filter(
        premises=premises, status__in=Contract.BLOCKING_STATUSES, date_to__gte=today
    ).exists():
        new_status = Premises.Status.BOOKED
    else:
        new_status = Premises.Status.FREE
    if premises.status != new_status:
        premises.status = new_status
        premises.save(update_fields=["status"])
    return premises


def _signature(version: ContractVersion, user: User, moment: datetime) -> str:
    """Имитация УКЭП: хеш от отпечатка версии договора, подписанта и времени.

    В рабочей системе здесь вызывается оператор ЭДО / КриптоПро, а в базе
    хранится открепленная подпись (.sig). Интерфейс функции при этом не меняется.
    """
    payload = f"{version.sha256}|{user.pk}|{user.username}|{moment.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()


# --- Заявки и бронирование ---------------------------------------------------

def submit_application(
    tenant: Tenant, premises: Premises, lease_from: date, lease_to: date, activity: str, comment: str = "",
    now: datetime | None = None,
) -> Application:
    now = now or timezone.now()
    today = timezone.localdate(now)
    if lease_from < today:
        raise LeasingError("Дата начала аренды не может быть в прошлом.")
    if lease_to <= lease_from:
        raise LeasingError("Дата окончания аренды должна быть позже даты начала.")
    if (lease_to - lease_from).days < 28:
        raise LeasingError("Минимальный срок аренды — один месяц.")
    if not activity.strip():
        raise LeasingError("Укажите вид деятельности.")

    with transaction.atomic():
        premises = _lock_premises(premises.pk)
        if premises.status == Premises.Status.REPAIR:
            raise PremisesUnavailable("Помещение на ремонте и недоступно для аренды.")
        if _has_blocking_contract(premises, lease_from, lease_to):
            raise PremisesUnavailable("На выбранный период помещение уже сдано или по нему оформляется договор.")
        application = Application.objects.create(
            tenant=tenant, premises=premises, lease_from=lease_from, lease_to=lease_to,
            activity=activity.strip(), comment=comment.strip(),
        )
        try:
            with transaction.atomic():
                Booking.objects.create(
                    application=application, premises=premises, lease_from=lease_from, lease_to=lease_to,
                    expires_at=add_business_days(now, settings.BOOKING_BUSINESS_DAYS),
                )
        except IntegrityError as exc:
            # Сработало ограничение booking_no_overlap.
            raise PremisesUnavailable("На выбранный период помещение уже забронировано другим арендатором.") from exc
        refresh_premises_status(premises, today)
    return application


@transaction.atomic
def approve_application(application: Application, manager: User) -> Application:
    application = Application.objects.select_for_update().get(pk=application.pk)
    if application.status != Application.Status.PENDING:
        raise LeasingError("Одобрить можно только заявку на рассмотрении.")
    application.status = Application.Status.APPROVED
    application.manager = manager
    application.decided_at = timezone.now()
    application.save(update_fields=["status", "manager", "decided_at"])
    return application


@transaction.atomic
def reject_application(application: Application, manager: User, reason: str) -> Application:
    application = Application.objects.select_for_update().get(pk=application.pk)
    if application.status not in (Application.Status.PENDING, Application.Status.APPROVED):
        raise LeasingError("Отклонить можно только заявку на рассмотрении или одобренную без договора.")
    if not reason.strip():
        raise LeasingError("Укажите причину отказа.")
    premises = _lock_premises(application.premises_id)
    application.status = Application.Status.REJECTED
    application.manager = manager
    application.reject_reason = reason.strip()
    application.decided_at = timezone.now()
    application.save(update_fields=["status", "manager", "reject_reason", "decided_at"])
    Booking.objects.filter(application=application).update(is_active=False)
    refresh_premises_status(premises)
    return application


def expire_bookings(now: datetime | None = None) -> int:
    """Снимает просроченные брони; незаключённые договоры по ним отменяются."""
    now = now or timezone.now()
    count = 0
    expired_ids = list(
        Booking.objects.filter(is_active=True, expires_at__lt=now).values_list("pk", flat=True)
    )
    for booking_id in expired_ids:
        with transaction.atomic():
            booking = Booking.objects.select_for_update().select_related("application").get(pk=booking_id)
            if not booking.is_active:
                continue
            premises = _lock_premises(booking.premises_id)
            booking.is_active = False
            booking.save(update_fields=["is_active"])
            application = booking.application
            Contract.objects.filter(
                application=application,
                status__in=(Contract.Status.DRAFT, Contract.Status.APPROVAL, Contract.Status.AWAITING_SIGNATURE),
            ).update(status=Contract.Status.CANCELLED)
            if application.status in (
                Application.Status.PENDING, Application.Status.APPROVED, Application.Status.CONTRACTED
            ):
                application.status = Application.Status.EXPIRED
                application.save(update_fields=["status"])
            refresh_premises_status(premises, timezone.localdate(now))
            count += 1
    return count


# --- Договоры ----------------------------------------------------------------

def _next_contract_number(year: int) -> str:
    prefix = f"АР-{year}-"
    last = Contract.objects.filter(number__startswith=prefix).aggregate(m=Max("number"))["m"]
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def render_contract_version(contract: Contract, user: User) -> ContractVersion:
    """Формирует новую версию текста договора по шаблону и сохраняет её отпечаток."""
    html = render_to_string("leasing/contract_document.html", {"c": contract, "today": timezone.localdate()})
    last_no = contract.versions.aggregate(m=Max("version_no"))["m"] or 0
    return ContractVersion.objects.create(
        contract=contract, version_no=last_no + 1, text_html=html,
        sha256=hashlib.sha256(html.encode()).hexdigest(), created_by=user,
    )


@transaction.atomic
def create_contract(application: Application, manager: User) -> Contract:
    application = Application.objects.select_for_update().select_related("premises", "tenant").get(pk=application.pk)
    if application.status != Application.Status.APPROVED:
        raise LeasingError("Договор формируется только по одобренной заявке.")
    if Contract.objects.filter(application=application).exists():
        raise LeasingError("По этой заявке договор уже сформирован.")
    premises = _lock_premises(application.premises_id)
    if _has_blocking_contract(premises, application.lease_from, application.lease_to):
        raise PremisesUnavailable("На этот период по помещению уже есть договор.")
    rent = premises.monthly_rent
    contract = Contract.objects.create(
        number=_next_contract_number(timezone.localdate().year),
        application=application,
        tenant=application.tenant,
        premises=premises,
        date_from=application.lease_from,
        date_to=application.lease_to,
        area_m2=premises.area_m2,
        rate_rub_m2=premises.rate_rub_m2,
        service_rate_rub_m2=settings.DEFAULT_SERVICE_RATE,
        deposit_rub=rent,
        penalty_pct=settings.DEFAULT_PENALTY_PCT,
        created_by=manager,
    )
    render_contract_version(contract, manager)
    application.status = Application.Status.CONTRACTED
    application.save(update_fields=["status"])
    return contract


def _lock_contract(contract: Contract) -> Contract:
    return Contract.objects.select_for_update().select_related("premises", "tenant").get(pk=contract.pk)


@transaction.atomic
def send_for_approval(contract: Contract, user: User) -> Contract:
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.DRAFT:
        raise LeasingError("На согласование отправляется только черновик.")
    contract.status = Contract.Status.APPROVAL
    contract.return_comment = ""
    contract.save(update_fields=["status", "return_comment"])
    return contract


@transaction.atomic
def return_for_revision(contract: Contract, user: User, comment: str) -> Contract:
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.APPROVAL:
        raise LeasingError("Вернуть на доработку можно только договор на согласовании.")
    if not comment.strip():
        raise LeasingError("Укажите, что нужно исправить.")
    contract.status = Contract.Status.DRAFT
    contract.return_comment = comment.strip()
    contract.save(update_fields=["status", "return_comment"])
    return contract


@transaction.atomic
def update_draft_terms(contract: Contract, user: User, **terms) -> Contract:
    """Менеджер правит условия черновика; создаётся новая версия текста."""
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.DRAFT:
        raise LeasingError("Изменять условия можно только в черновике.")
    allowed = {"rate_rub_m2", "service_rate_rub_m2", "deposit_rub", "penalty_pct"}
    changed = []
    for field, value in terms.items():
        if field in allowed and value is not None:
            if value < 0:
                raise LeasingError("Значения условий не могут быть отрицательными.")
            setattr(contract, field, value)
            changed.append(field)
    if changed:
        contract.save(update_fields=changed)
        render_contract_version(contract, user)
    return contract


@transaction.atomic
def approve_contract(contract: Contract, director: User) -> Contract:
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.APPROVAL:
        raise LeasingError("Утвердить можно только договор на согласовании.")
    now = timezone.now()
    contract.status = Contract.Status.AWAITING_SIGNATURE
    contract.approved_by = director
    contract.director_signed_at = now
    contract.director_signature = _signature(contract.current_version, director, now)
    contract.save(update_fields=["status", "approved_by", "director_signed_at", "director_signature"])
    return contract


@transaction.atomic
def sign_contract(contract: Contract, tenant_user: User) -> Contract:
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.AWAITING_SIGNATURE:
        raise LeasingError("Договор не ожидает подписи арендатора.")
    if contract.tenant.user_id != tenant_user.pk:
        raise LeasingError("Подписать договор может только арендатор, указанный в нём.")
    premises = _lock_premises(contract.premises_id)
    booking = Booking.objects.select_for_update().filter(application=contract.application).first()
    if booking is not None and (not booking.is_active or booking.expires_at < timezone.now()):
        raise LeasingError("Срок брони истёк — договор нельзя подписать. Подайте новую заявку.")
    now = timezone.now()
    contract.status = Contract.Status.ACTIVE
    contract.tenant_signed_at = now
    contract.tenant_signature = _signature(contract.current_version, tenant_user, now)
    contract.save(update_fields=["status", "tenant_signed_at", "tenant_signature"])
    if booking is not None:
        booking.is_active = False
        booking.save(update_fields=["is_active"])
    refresh_premises_status(premises)

    from finance.services import create_deposit_invoice

    create_deposit_invoice(contract)
    return contract


@transaction.atomic
def terminate_contract(contract: Contract, user: User, end_date: date, reason: str) -> Contract:
    contract = _lock_contract(contract)
    if contract.status != Contract.Status.ACTIVE:
        raise LeasingError("Расторгнуть можно только действующий договор.")
    if not reason.strip():
        raise LeasingError("Укажите основание расторжения.")
    if end_date < contract.date_from:
        raise LeasingError("Дата расторжения раньше начала договора.")
    premises = _lock_premises(contract.premises_id)
    contract.status = Contract.Status.TERMINATED
    contract.date_to = max(min(contract.date_to, end_date), contract.date_from + timedelta(days=1))
    contract.return_comment = f"Расторгнут: {reason.strip()}"
    contract.save(update_fields=["status", "date_to", "return_comment"])
    refresh_premises_status(premises)
    return contract


def complete_expired_contracts(today: date | None = None) -> int:
    """Переводит в «Завершён» договоры с истёкшим сроком."""
    today = today or timezone.localdate()
    count = 0
    for contract_id in Contract.objects.filter(status=Contract.Status.ACTIVE, date_to__lt=today).values_list("pk", flat=True):
        with transaction.atomic():
            contract = Contract.objects.select_for_update().get(pk=contract_id)
            contract.status = Contract.Status.COMPLETED
            contract.save(update_fields=["status"])
            refresh_premises_status(_lock_premises(contract.premises_id), today)
            count += 1
    return count


def refresh_all_premises(today: date | None = None) -> None:
    for premises in Premises.objects.all():
        refresh_premises_status(premises, today)
