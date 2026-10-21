from datetime import date

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Tenant, User
from accounts.permissions import role_required
from core.forms import BootstrapMixin

from . import services
from .models import Invoice, Payment

ACCOUNTING = (User.Role.ACCOUNTANT, User.Role.DIRECTOR)


class PeriodForm(BootstrapMixin, forms.Form):
    period = forms.CharField(label="Месяц", widget=forms.TextInput(attrs={"type": "month"}))

    def clean_period(self):
        raw = self.cleaned_data["period"]
        try:
            year, month = raw.split("-")
            return date(int(year), int(month), 1)
        except ValueError as exc:
            raise forms.ValidationError("Укажите месяц в формате ГГГГ-ММ.") from exc


class StatementForm(BootstrapMixin, forms.Form):
    file = forms.FileField(label="Выписка банка (CSV)")


class AssignForm(BootstrapMixin, forms.Form):
    tenant = forms.ModelChoiceField(label="Арендатор", queryset=Tenant.objects.all())


@role_required(*ACCOUNTING)
def finance_home(request):
    today = timezone.localdate()
    status = request.GET.get("status", "unpaid")
    invoices = Invoice.objects.select_related("contract", "contract__tenant", "contract__premises")
    if status == "unpaid":
        invoices = invoices.exclude(status=Invoice.Status.PAID)
    elif status == "overdue":
        invoices = invoices.exclude(status=Invoice.Status.PAID).filter(due_date__lt=today)
    elif status in Invoice.Status.values:
        invoices = invoices.filter(status=status)
    unmatched = Payment.objects.filter(tenant__isnull=True).order_by("-paid_at")
    recent_payments = Payment.objects.filter(tenant__isnull=False).select_related("tenant").prefetch_related("allocations")[:15]
    return render(request, "finance/home.html", {
        "invoices": invoices.order_by("due_date", "id")[:200],
        "status": status,
        "period_form": PeriodForm(initial={"period": f"{today:%Y-%m}"}),
        "statement_form": StatementForm(),
        "assign_form": AssignForm(),
        "unmatched": unmatched,
        "recent_payments": recent_payments,
        "today": today,
    })


@require_POST
@role_required(User.Role.ACCOUNTANT)
def generate(request):
    form = PeriodForm(request.POST)
    if form.is_valid():
        created = services.generate_invoices(form.cleaned_data["period"])
        period = form.cleaned_data["period"]
        if created:
            messages.success(request, f"Выставлено счетов за {period:%m.%Y}: {len(created)}.")
        else:
            messages.info(request, f"Новых счетов за {period:%m.%Y} нет — все уже выставлены.")
    else:
        messages.error(request, "Укажите месяц начисления.")
    return redirect("finance:home")


@require_POST
@role_required(User.Role.ACCOUNTANT)
def penalties(request):
    updated = services.accrue_penalties()
    messages.info(request, f"Пени пересчитаны, изменено счетов: {updated}.")
    return redirect("finance:home")


@role_required(User.Role.ACCOUNTANT)
def import_statement(request):
    form = StatementForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["file"].read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("cp1251")
        report = services.import_statement(content)
        messages.success(
            request,
            f"Обработано строк: {report.total_rows}. Сопоставлено: {len(report.matched)}, "
            f"требуют разбора: {len(report.unmatched)}, повторы: {len(report.duplicates)}, ошибки: {len(report.errors)}.",
        )
    return render(request, "finance/import.html", {"form": form, "report": report})


@require_POST
@role_required(User.Role.ACCOUNTANT)
def assign(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    form = AssignForm(request.POST)
    if form.is_valid():
        try:
            services.assign_payment(payment, form.cleaned_data["tenant"])
            messages.success(request, f"Платёж №{payment.bank_doc_no} привязан к {form.cleaned_data['tenant'].legal_name}.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("finance:home")


def invoice_detail(request, pk):
    from django.contrib.auth.views import redirect_to_login
    from django.core.exceptions import PermissionDenied

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    invoice = get_object_or_404(Invoice.objects.select_related("contract", "contract__tenant", "contract__premises"), pk=pk)
    user = request.user
    is_owner = hasattr(user, "tenant") and invoice.contract.tenant_id == user.tenant.pk
    if not (is_owner or user.has_role(*ACCOUNTING, User.Role.MANAGER)):
        raise PermissionDenied
    return render(request, "finance/invoice.html", {
        "i": invoice, "allocations": invoice.allocations.select_related("payment"),
    })
