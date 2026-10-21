from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from leasing.forms import ApplicationForm
from leasing.models import Booking, Contract
from leasing.services import LeasingError, submit_application

from .models import Floor, Premises, PremisesType
from .selectors import filter_premises


def catalog_list(request):
    premises, filters = filter_premises(request.GET)
    floors = Floor.objects.all()
    plan_floor_number = filters["floor"] or (floors.first().number if floors else None)
    plan_floor = floors.filter(number=plan_floor_number).first() if plan_floor_number else None
    matched_ids = set(premises.values_list("pk", flat=True))
    plan_premises = (
        Premises.objects.filter(floor=plan_floor).select_related("type") if plan_floor else Premises.objects.none()
    )
    context = {
        "premises": premises,
        "filters": filters,
        "floors": floors,
        "types": PremisesType.objects.all(),
        "plan_floor": plan_floor,
        "plan_premises": plan_premises,
        "matched_ids": matched_ids,
        "statuses": Premises.Status,
    }
    return render(request, "catalog/list.html", context)


def premises_detail(request, pk):
    premises = get_object_or_404(Premises.objects.select_related("floor", "type"), pk=pk)
    user = request.user
    is_tenant = user.is_authenticated and hasattr(user, "tenant")
    form = ApplicationForm(request.POST or None) if is_tenant else None

    if request.method == "POST":
        if not is_tenant:
            messages.error(request, "Подать заявку может только зарегистрированный арендатор.")
            return redirect("login")
        if form.is_valid():
            try:
                application = submit_application(
                    user.tenant, premises, form.cleaned_data["lease_from"], form.cleaned_data["lease_to"],
                    form.cleaned_data["activity"], form.cleaned_data["comment"],
                )
            except LeasingError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request,
                    f"Заявка №{application.pk} принята. Помещение забронировано до "
                    f"{timezone.localtime(application.booking.expires_at):%d.%m.%Y %H:%M}.",
                )
                return redirect("leasing:cabinet")

    today = timezone.localdate()
    busy_periods = list(
        Booking.objects.filter(premises=premises, is_active=True, lease_to__gte=today)
        .values("lease_from", "lease_to")
    ) + list(
        Contract.objects.filter(premises=premises, status__in=Contract.BLOCKING_STATUSES, date_to__gte=today)
        .values("date_from", "date_to")
    )
    busy = sorted(
        ({"from": p.get("lease_from") or p.get("date_from"), "to": p.get("lease_to") or p.get("date_to")} for p in busy_periods),
        key=lambda p: p["from"],
    )
    return render(request, "catalog/detail.html", {"p": premises, "form": form, "busy": busy, "is_tenant": is_tenant})
