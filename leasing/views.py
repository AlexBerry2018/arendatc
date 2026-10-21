from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import role_required, tenant_required
from finance.models import Invoice

from . import services
from .forms import DraftTermsForm, RejectForm, ReturnForm, TerminateForm
from .models import Application, Contract

STAFF = (User.Role.MANAGER, User.Role.DIRECTOR, User.Role.ACCOUNTANT)


# --- Кабинет арендатора -------------------------------------------------------

@tenant_required
def cabinet(request):
    tenant = request.user.tenant
    invoices = Invoice.objects.filter(contract__tenant=tenant).select_related("contract").order_by("-period", "-id")
    debt = sum((i.debt for i in invoices if i.debt > 0), 0)
    overdue = sum((i.debt for i in invoices if i.is_overdue), 0)
    context = {
        "tenant": tenant,
        "applications": tenant.applications.select_related("premises", "booking").all()[:20],
        "contracts": tenant.contracts.select_related("premises").all(),
        "active_count": tenant.contracts.filter(status="active").count(),
        "invoices": invoices[:24],
        "debt": debt,
        "overdue": overdue,
    }
    return render(request, "leasing/cabinet.html", context)


# --- Заявки (менеджер) --------------------------------------------------------

@role_required(User.Role.MANAGER, User.Role.DIRECTOR)
def applications(request):
    status = request.GET.get("status", "active")
    qs = Application.objects.select_related("tenant", "premises", "booking", "manager")
    if status == "active":
        qs = qs.filter(status__in=(Application.Status.PENDING, Application.Status.APPROVED))
    elif status in Application.Status.values:
        qs = qs.filter(status=status)
    return render(request, "leasing/applications.html", {
        "applications": qs.order_by("booking__expires_at", "created_at"),
        "status": status,
        "statuses": Application.Status.choices,
        "reject_form": RejectForm(),
    })


@require_POST
@role_required(User.Role.MANAGER)
def application_action(request, pk, action):
    application = get_object_or_404(Application, pk=pk)
    try:
        if action == "approve":
            services.approve_application(application, request.user)
            messages.success(request, f"Заявка №{pk} одобрена. Сформируйте договор.")
        elif action == "reject":
            form = RejectForm(request.POST)
            reason = form.data.get("reason", "")
            services.reject_application(application, request.user, reason)
            messages.info(request, f"Заявка №{pk} отклонена, бронь снята.")
        elif action == "contract":
            contract = services.create_contract(application, request.user)
            messages.success(request, f"Сформирован договор {contract.number}.")
            return redirect("leasing:contract", pk=contract.pk)
        else:
            raise PermissionDenied
    except services.LeasingError as exc:
        messages.error(request, str(exc))
    return redirect("leasing:applications")


# --- Договоры -----------------------------------------------------------------

def _contract_for_user(request, pk) -> Contract:
    contract = get_object_or_404(Contract.objects.select_related("tenant", "premises", "premises__floor"), pk=pk)
    user = request.user
    if not user.is_authenticated:
        raise PermissionDenied
    if user.has_role(*STAFF):
        return contract
    if hasattr(user, "tenant") and contract.tenant_id == user.tenant.pk and contract.status != Contract.Status.DRAFT:
        return contract
    raise PermissionDenied


@role_required(*STAFF)
def contracts(request):
    status = request.GET.get("status", "")
    qs = Contract.objects.select_related("tenant", "premises")
    if status in Contract.Status.values:
        qs = qs.filter(status=status)
    return render(request, "leasing/contracts.html", {
        "contracts": qs, "status": status, "statuses": Contract.Status.choices,
    })


@login_required
def contract_detail(request, pk):
    contract = _contract_for_user(request, pk)
    user = request.user
    ctx = {
        "c": contract,
        "version": contract.current_version,
        "versions": contract.versions.order_by("-version_no"),
        "invoices": contract.invoices.order_by("period", "kind"),
        "booking": getattr(contract.application, "booking", None) if contract.application_id else None,
        "can_edit": user.has_role(User.Role.MANAGER) and contract.status == Contract.Status.DRAFT,
        "can_send": user.has_role(User.Role.MANAGER) and contract.status == Contract.Status.DRAFT,
        "can_approve": user.has_role(User.Role.DIRECTOR) and contract.status == Contract.Status.APPROVAL,
        "can_sign": hasattr(user, "tenant") and contract.tenant_id == user.tenant.pk
                    and contract.status == Contract.Status.AWAITING_SIGNATURE,
        "can_terminate": user.has_role(User.Role.MANAGER, User.Role.DIRECTOR) and contract.status == Contract.Status.ACTIVE,
        "terms_form": DraftTermsForm(initial={
            "rate_rub_m2": contract.rate_rub_m2, "service_rate_rub_m2": contract.service_rate_rub_m2,
            "deposit_rub": contract.deposit_rub, "penalty_pct": contract.penalty_pct,
        }),
        "return_form": ReturnForm(),
        "terminate_form": TerminateForm(),
    }
    return render(request, "leasing/contract_detail.html", ctx)


@login_required
def contract_document(request, pk):
    """Печатная форма текущей (или указанной) версии договора."""
    contract = _contract_for_user(request, pk)
    version_no = request.GET.get("v")
    version = contract.versions.filter(version_no=version_no).first() if version_no else contract.current_version
    if version is None:
        return HttpResponse("Текст договора ещё не сформирован.", status=404)
    return render(request, "leasing/contract_print.html", {"c": contract, "version": version})


@require_POST
@login_required
def contract_action(request, pk, action):
    contract = _contract_for_user(request, pk)
    user = request.user
    try:
        if action == "terms" and user.has_role(User.Role.MANAGER):
            form = DraftTermsForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Проверьте значения условий договора.")
                return redirect("leasing:contract", pk=pk)
            services.update_draft_terms(contract, user, **form.cleaned_data)
            messages.success(request, "Условия обновлены, создана новая версия договора.")
        elif action == "send" and user.has_role(User.Role.MANAGER):
            services.send_for_approval(contract, user)
            messages.success(request, "Договор отправлен руководителю на утверждение.")
        elif action == "return" and user.has_role(User.Role.DIRECTOR):
            services.return_for_revision(contract, user, request.POST.get("comment", ""))
            messages.info(request, "Договор возвращён менеджеру на доработку.")
        elif action == "approve" and user.has_role(User.Role.DIRECTOR):
            services.approve_contract(contract, user)
            messages.success(request, "Договор утверждён и подписан руководителем. Ожидает подписи арендатора.")
        elif action == "sign" and hasattr(user, "tenant"):
            services.sign_contract(contract, user)
            messages.success(request, "Договор подписан и вступил в силу. Выставлен счёт на обеспечительный платёж.")
        elif action == "terminate" and user.has_role(User.Role.MANAGER, User.Role.DIRECTOR):
            form = TerminateForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Укажите дату и основание расторжения.")
                return redirect("leasing:contract", pk=pk)
            services.terminate_contract(contract, user, form.cleaned_data["end_date"], form.cleaned_data["reason"])
            messages.info(request, "Договор расторгнут.")
        else:
            raise PermissionDenied("Это действие вам недоступно.")
    except services.LeasingError as exc:
        messages.error(request, str(exc))
    return redirect("leasing:contract", pk=pk)
