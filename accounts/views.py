from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render

from .forms import TenantRegistrationForm


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = TenantRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Регистрация завершена. Теперь можно подать заявку на помещение.")
        return redirect("catalog:list")
    return render(request, "accounts/register.html", {"form": form})
