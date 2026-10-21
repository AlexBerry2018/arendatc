from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import F, Func, Q

from accounts.models import Tenant
from catalog.models import Premises

CENT = Decimal("0.01")


class DateRange(Func):
    """SQL daterange(lower, upper, '[]') — период с включёнными границами."""

    function = "DATERANGE"
    output_field = DateRangeField()


class Application(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "На рассмотрении"
        APPROVED = "approved", "Одобрена"
        REJECTED = "rejected", "Отклонена"
        EXPIRED = "expired", "Истекла бронь"
        CONTRACTED = "contracted", "Оформлен договор"

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="applications", verbose_name="арендатор")
    premises = models.ForeignKey(Premises, on_delete=models.PROTECT, related_name="applications", verbose_name="помещение")
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_applications", verbose_name="менеджер",
    )
    lease_from = models.DateField("начало аренды")
    lease_to = models.DateField("окончание аренды")
    activity = models.CharField("вид деятельности", max_length=255)
    comment = models.TextField("комментарий арендатора", blank=True)
    status = models.CharField("статус", max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reject_reason = models.TextField("причина отказа", blank=True)
    created_at = models.DateTimeField("создана", auto_now_add=True)
    decided_at = models.DateTimeField("решение принято", null=True, blank=True)

    class Meta:
        verbose_name = "заявка"
        verbose_name_plural = "заявки"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(lease_to__gt=F("lease_from")), name="application_period_valid"),
        ]

    def __str__(self):
        return f"Заявка №{self.pk} на {self.premises.code}"


class Booking(models.Model):
    """Временная блокировка помещения на период аренды, пока идёт согласование."""

    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="booking")
    premises = models.ForeignKey(Premises, on_delete=models.PROTECT, related_name="bookings")
    lease_from = models.DateField()
    lease_to = models.DateField()
    expires_at = models.DateTimeField("бронь действует до")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "бронь"
        verbose_name_plural = "брони"
        constraints = [
            # Требование целостности: две активные брони одного помещения
            # не могут пересекаться по периоду аренды. Проверяет сама СУБД.
            ExclusionConstraint(
                name="booking_no_overlap",
                expressions=[
                    ("premises", RangeOperators.EQUAL),
                    (DateRange("lease_from", "lease_to", RangeBoundary(inclusive_upper=True)), RangeOperators.OVERLAPS),
                ],
                condition=Q(is_active=True),
            ),
        ]

    def __str__(self):
        return f"Бронь {self.premises.code} до {self.expires_at:%d.%m.%Y %H:%M}"


class Contract(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        APPROVAL = "approval", "На согласовании"
        AWAITING_SIGNATURE = "awaiting_signature", "Ожидает подписи арендатора"
        ACTIVE = "active", "Действует"
        TERMINATED = "terminated", "Расторгнут"
        COMPLETED = "completed", "Завершён"
        CANCELLED = "cancelled", "Отменён"

    BLOCKING_STATUSES = (Status.DRAFT, Status.APPROVAL, Status.AWAITING_SIGNATURE, Status.ACTIVE)

    number = models.CharField("номер", max_length=32, unique=True)
    application = models.OneToOneField(
        Application, on_delete=models.PROTECT, null=True, blank=True, related_name="contract"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="contracts", verbose_name="арендатор")
    premises = models.ForeignKey(Premises, on_delete=models.PROTECT, related_name="contracts", verbose_name="помещение")
    date_from = models.DateField("начало")
    date_to = models.DateField("окончание")
    area_m2 = models.DecimalField("площадь, м²", max_digits=8, decimal_places=2)
    rate_rub_m2 = models.DecimalField("ставка аренды, руб./м²", max_digits=10, decimal_places=2)
    service_rate_rub_m2 = models.DecimalField("ставка эксплуатации, руб./м²", max_digits=10, decimal_places=2)
    deposit_rub = models.DecimalField("обеспечительный платёж, руб.", max_digits=12, decimal_places=2)
    penalty_pct = models.DecimalField("пени, % в день", max_digits=5, decimal_places=3)
    status = models.CharField("статус", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_contracts", verbose_name="подготовил"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_contracts", verbose_name="утвердил",
    )
    director_signed_at = models.DateTimeField(null=True, blank=True)
    director_signature = models.CharField("подпись руководителя (хеш)", max_length=64, blank=True)
    tenant_signed_at = models.DateTimeField(null=True, blank=True)
    tenant_signature = models.CharField("подпись арендатора (хеш)", max_length=64, blank=True)
    return_comment = models.TextField("комментарий при возврате", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "договор аренды"
        verbose_name_plural = "договоры аренды"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(date_to__gt=F("date_from")), name="contract_period_valid"),
            # Помещение нельзя сдать дважды на пересекающиеся периоды.
            ExclusionConstraint(
                name="contract_no_overlap",
                expressions=[
                    ("premises", RangeOperators.EQUAL),
                    (DateRange("date_from", "date_to", RangeBoundary(inclusive_upper=True)), RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=["draft", "approval", "awaiting_signature", "active"]),
            ),
        ]

    def __str__(self):
        return f"Договор {self.number}"

    @property
    def monthly_rent(self) -> Decimal:
        return (self.area_m2 * self.rate_rub_m2).quantize(CENT, ROUND_HALF_UP)

    @property
    def monthly_service(self) -> Decimal:
        return (self.area_m2 * self.service_rate_rub_m2).quantize(CENT, ROUND_HALF_UP)

    @property
    def current_version(self):
        return self.versions.order_by("-version_no").first()


class ContractVersion(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveIntegerField()
    text_html = models.TextField("текст договора")
    sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "версия договора"
        verbose_name_plural = "версии договора"
        constraints = [
            models.UniqueConstraint(fields=["contract", "version_no"], name="contract_version_unique"),
        ]

    def __str__(self):
        return f"{self.contract.number} v{self.version_no}"
