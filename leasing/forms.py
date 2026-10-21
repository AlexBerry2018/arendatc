from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.forms import BootstrapMixin


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class ApplicationForm(BootstrapMixin, forms.Form):
    lease_from = forms.DateField(label="Начало аренды", widget=DateInput())
    lease_to = forms.DateField(label="Окончание аренды", widget=DateInput())
    activity = forms.CharField(label="Вид деятельности", max_length=255,
                               widget=forms.TextInput(attrs={"placeholder": "Например: магазин одежды"}))
    comment = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        start = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        self.fields["lease_from"].initial = start
        self.fields["lease_to"].initial = start.replace(year=start.year + 1) - timedelta(days=1)

    def clean(self):
        data = super().clean()
        if data.get("lease_from") and data.get("lease_to") and data["lease_to"] <= data["lease_from"]:
            raise forms.ValidationError("Дата окончания должна быть позже даты начала.")
        return data


class RejectForm(BootstrapMixin, forms.Form):
    reason = forms.CharField(label="Причина отказа", widget=forms.Textarea(attrs={"rows": 2}))


class ReturnForm(BootstrapMixin, forms.Form):
    comment = forms.CharField(label="Что исправить", widget=forms.Textarea(attrs={"rows": 2}))


class TerminateForm(BootstrapMixin, forms.Form):
    end_date = forms.DateField(label="Дата расторжения", widget=DateInput())
    reason = forms.CharField(label="Основание", widget=forms.Textarea(attrs={"rows": 2}))


class DraftTermsForm(BootstrapMixin, forms.Form):
    rate_rub_m2 = forms.DecimalField(label="Ставка аренды, ₽/м²", min_value=Decimal("0.01"), decimal_places=2)
    service_rate_rub_m2 = forms.DecimalField(label="Эксплуатация, ₽/м²", min_value=Decimal("0"), decimal_places=2)
    deposit_rub = forms.DecimalField(label="Обеспечительный платёж, ₽", min_value=Decimal("0"), decimal_places=2)
    penalty_pct = forms.DecimalField(label="Пени, % в день", min_value=Decimal("0"), max_value=Decimal("1"), decimal_places=3)
