from decimal import Decimal

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import Tenant
from leasing.models import Contract

ZERO = Decimal("0.00")


class Invoice(models.Model):
    class Kind(models.TextChoices):
        RENT = "rent", "Аренда и эксплуатация"
        DEPOSIT = "deposit", "Обеспечительный платёж"

    class Status(models.TextChoices):
        ISSUED = "issued", "Выставлен"
        PARTIALLY_PAID = "partially_paid", "Оплачен частично"
        PAID = "paid", "Оплачен"

    number = models.CharField("номер", max_length=32, unique=True)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="invoices", verbose_name="договор")
    kind = models.CharField("вид", max_length=10, choices=Kind.choices, default=Kind.RENT)
    period = models.DateField("период (первое число месяца)")
    rent_amount = models.DecimalField("аренда, руб.", max_digits=12, decimal_places=2)
    service_amount = models.DecimalField("эксплуатация, руб.", max_digits=12, decimal_places=2, default=ZERO)
    penalty_amount = models.DecimalField("пени, руб.", max_digits=12, decimal_places=2, default=ZERO)
    paid_amount = models.DecimalField("оплачено, руб.", max_digits=12, decimal_places=2, default=ZERO)
    due_date = models.DateField("оплатить до")
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.ISSUED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "счёт"
        verbose_name_plural = "счета"
        ordering = ["due_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "period", "kind"], name="invoice_unique_period"),
            models.CheckConstraint(condition=Q(paid_amount__gte=0), name="invoice_paid_nonnegative"),
            models.CheckConstraint(
                condition=Q(paid_amount__lte=F("rent_amount") + F("service_amount") + F("penalty_amount")),
                name="invoice_not_overpaid",
            ),
        ]

    def __str__(self):
        return f"Счёт {self.number}"

    @property
    def total(self) -> Decimal:
        return self.rent_amount + self.service_amount + self.penalty_amount

    @property
    def debt(self) -> Decimal:
        return self.total - self.paid_amount

    @property
    def is_overdue(self) -> bool:
        return self.debt > 0 and self.due_date < timezone.localdate()

    def refresh_status(self):
        if self.paid_amount <= 0:
            self.status = self.Status.ISSUED
        elif self.paid_amount < self.total:
            self.status = self.Status.PARTIALLY_PAID
        else:
            self.status = self.Status.PAID


class Payment(models.Model):
    class Source(models.TextChoices):
        IMPORT = "import", "Банковская выписка"
        MANUAL = "manual", "Ручной ввод"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, null=True, blank=True, related_name="payments", verbose_name="арендатор"
    )
    payer_inn = models.CharField("ИНН плательщика", max_length=12)
    amount = models.DecimalField("сумма, руб.", max_digits=12, decimal_places=2)
    paid_at = models.DateField("дата платежа")
    bank_doc_no = models.CharField("номер платёжного документа", max_length=32)
    purpose = models.CharField("назначение платежа", max_length=255, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.IMPORT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "платёж"
        verbose_name_plural = "платежи"
        ordering = ["-paid_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            # Защита от повторной загрузки одной и той же выписки.
            models.UniqueConstraint(fields=["bank_doc_no", "paid_at", "payer_inn"], name="payment_unique_bank_doc"),
        ]

    def __str__(self):
        return f"п/п №{self.bank_doc_no} от {self.paid_at:%d.%m.%Y} на {self.amount} руб."

    @property
    def allocated_amount(self) -> Decimal:
        return sum((a.amount for a in self.allocations.all()), ZERO)

    @property
    def unallocated_amount(self) -> Decimal:
        return self.amount - self.allocated_amount


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "распределение платежа"
        verbose_name_plural = "распределения платежей"
        constraints = [
            models.UniqueConstraint(fields=["payment", "invoice"], name="allocation_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive"),
        ]
