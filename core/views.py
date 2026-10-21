from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.models import User
from accounts.permissions import role_required

from . import reports


def home(request):
    """Каждая роль попадает в свой рабочий раздел."""
    user = request.user
    if not user.is_authenticated:
        return redirect("catalog:list")
    if hasattr(user, "tenant"):
        return redirect("leasing:cabinet")
    if user.role == User.Role.MANAGER:
        return redirect("leasing:applications")
    if user.role == User.Role.ACCOUNTANT:
        return redirect("finance:home")
    if user.role in (User.Role.DIRECTOR, User.Role.ADMIN) or user.is_superuser:
        return redirect("dashboard")
    return redirect("catalog:list")


@role_required(User.Role.DIRECTOR, User.Role.MANAGER, User.Role.ACCOUNTANT)
def dashboard(request):
    today = timezone.localdate()
    floors, overall = reports.occupancy_by_floor()
    context = {
        "floors": floors,
        "overall": overall,
        "revenue": reports.revenue_for_month(today),
        "debtors": reports.debtors(today),
        "expiring": reports.expiring_contracts(60, today),
        "pipeline": reports.pipeline_counts(),
        "today": today,
    }
    return render(request, "core/dashboard.html", context)
