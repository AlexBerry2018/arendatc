from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

from .validators import validate_inn, validate_ogrn


class User(AbstractUser):
    class Role(models.TextChoices):
        TENANT = "tenant", "Арендатор"
        MANAGER = "manager", "Менеджер по аренде"
        ACCOUNTANT = "accountant", "Бухгалтер"
        DIRECTOR = "director", "Руководитель"
        ADMIN = "admin", "Администратор"

    role = models.CharField("роль", max_length=20, choices=Role.choices, default=Role.TENANT)

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    def has_role(self, *roles) -> bool:
        if self.is_superuser or self.role == self.Role.ADMIN:
            return True
        return self.role in roles

    @property
    def is_tenant(self):
        return self.role == self.Role.TENANT

    @property
    def is_staff_role(self):
        return self.role in (self.Role.MANAGER, self.Role.ACCOUNTANT, self.Role.DIRECTOR, self.Role.ADMIN) or self.is_superuser


class Tenant(models.Model):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="tenant", verbose_name="пользователь")
    legal_name = models.CharField("наименование", max_length=255)
    inn = models.CharField("ИНН", max_length=12, unique=True, validators=[validate_inn])
    ogrn = models.CharField("ОГРН/ОГРНИП", max_length=15, validators=[validate_ogrn])
    kpp = models.CharField("КПП", max_length=9, blank=True)
    address = models.CharField("юридический адрес", max_length=255, blank=True)
    phone = models.CharField("телефон", max_length=32, blank=True)
    bank_details = models.JSONField("банковские реквизиты", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "арендатор"
        verbose_name_plural = "арендаторы"
        ordering = ["legal_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(inn__regex=r"^(\d{10}|\d{12})$"),
                name="tenant_inn_format",
            ),
        ]

    def __str__(self):
        return f"{self.legal_name} (ИНН {self.inn})"
