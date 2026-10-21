from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.db.models import Q

CENT = Decimal("0.01")


class Floor(models.Model):
    number = models.SmallIntegerField("номер этажа", unique=True)
    name = models.CharField("название", max_length=100, blank=True)
    plan_width = models.PositiveIntegerField("ширина плана", default=1000)
    plan_height = models.PositiveIntegerField("высота плана", default=520)

    class Meta:
        verbose_name = "этаж"
        verbose_name_plural = "этажи"
        ordering = ["number"]

    def __str__(self):
        return self.name or f"{self.number} этаж"


class PremisesType(models.Model):
    name = models.CharField("название", max_length=100, unique=True)

    class Meta:
        verbose_name = "тип помещения"
        verbose_name_plural = "типы помещений"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Premises(models.Model):
    class Status(models.TextChoices):
        FREE = "free", "Свободно"
        BOOKED = "booked", "Забронировано"
        LEASED = "leased", "Сдано"
        REPAIR = "repair", "На ремонте"

    floor = models.ForeignKey(Floor, on_delete=models.PROTECT, related_name="premises", verbose_name="этаж")
    type = models.ForeignKey(PremisesType, on_delete=models.PROTECT, related_name="premises", verbose_name="тип")
    code = models.CharField("номер помещения", max_length=20, unique=True)
    area_m2 = models.DecimalField("площадь, м²", max_digits=8, decimal_places=2)
    rate_rub_m2 = models.DecimalField("ставка, руб./м² в месяц", max_digits=10, decimal_places=2)
    status = models.CharField("статус", max_length=10, choices=Status.choices, default=Status.FREE, db_index=True)
    description = models.TextField("описание", blank=True)
    # Положение прямоугольника на SVG-плане этажа.
    plan_x = models.PositiveIntegerField(default=0)
    plan_y = models.PositiveIntegerField(default=0)
    plan_w = models.PositiveIntegerField(default=100)
    plan_h = models.PositiveIntegerField(default=80)

    class Meta:
        verbose_name = "помещение"
        verbose_name_plural = "помещения"
        ordering = ["floor__number", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(area_m2__gt=0), name="premises_area_positive"),
            models.CheckConstraint(condition=Q(rate_rub_m2__gt=0), name="premises_rate_positive"),
        ]

    def __str__(self):
        return f"{self.code} — {self.area_m2} м²"

    @property
    def monthly_rent(self) -> Decimal:
        return (self.area_m2 * self.rate_rub_m2).quantize(CENT, ROUND_HALF_UP)
