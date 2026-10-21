"""Ролевой доступ для веб-страниц."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Пускает пользователя с одной из ролей; администратор проходит всегда."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.has_role(*roles):
                raise PermissionDenied("Недостаточно прав для этого раздела.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def tenant_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not hasattr(request.user, "tenant"):
            raise PermissionDenied("Раздел доступен только арендаторам.")
        return view(request, *args, **kwargs)

    return wrapper
