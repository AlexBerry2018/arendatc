from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from core.forms import BootstrapMixin

from .models import Tenant, User
from .validators import validate_inn, validate_ogrn


class TenantRegistrationForm(BootstrapMixin, UserCreationForm):
    email = forms.EmailField(label="Email")
    legal_name = forms.CharField(label="Наименование организации или ИП", max_length=255)
    inn = forms.CharField(label="ИНН", max_length=12, validators=[validate_inn])
    ogrn = forms.CharField(label="ОГРН / ОГРНИП", max_length=15, validators=[validate_ogrn])
    kpp = forms.CharField(label="КПП", max_length=9, required=False)
    address = forms.CharField(label="Юридический адрес", max_length=255, required=False)
    phone = forms.CharField(label="Телефон", max_length=32, required=False)
    agree = forms.BooleanField(label="Согласен на обработку персональных данных (152-ФЗ)")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_inn(self):
        inn = self.cleaned_data["inn"].strip()
        if Tenant.objects.filter(inn=inn).exists():
            raise forms.ValidationError("Организация с таким ИНН уже зарегистрирована.")
        return inn

    def clean(self):
        data = super().clean()
        inn, ogrn = data.get("inn"), data.get("ogrn")
        if inn and ogrn:
            if len(inn) == 10 and len(ogrn) != 13:
                self.add_error("ogrn", "Для организации (ИНН из 10 цифр) нужен ОГРН из 13 цифр.")
            if len(inn) == 12 and len(ogrn) != 15:
                self.add_error("ogrn", "Для ИП (ИНН из 12 цифр) нужен ОГРНИП из 15 цифр.")
        return data

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.role = User.Role.TENANT
        user.save()
        Tenant.objects.create(
            user=user,
            legal_name=self.cleaned_data["legal_name"],
            inn=self.cleaned_data["inn"],
            ogrn=self.cleaned_data["ogrn"],
            kpp=self.cleaned_data.get("kpp", ""),
            address=self.cleaned_data.get("address", ""),
            phone=self.cleaned_data.get("phone", ""),
        )
        return user
