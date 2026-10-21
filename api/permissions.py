from rest_framework.permissions import BasePermission

from accounts.models import User


def has_role(*roles):
    class _HasRole(BasePermission):
        message = "Действие недоступно для вашей роли."

        def has_permission(self, request, view):
            return bool(request.user and request.user.is_authenticated and request.user.has_role(*roles))

    _HasRole.__name__ = "HasRole_" + "_".join(roles)
    return _HasRole


class IsTenant(BasePermission):
    message = "Действие доступно только арендатору."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and hasattr(request.user, "tenant"))


IsManager = has_role(User.Role.MANAGER)
IsDirector = has_role(User.Role.DIRECTOR)
IsAccountant = has_role(User.Role.ACCOUNTANT)
IsStaffRole = has_role(User.Role.MANAGER, User.Role.ACCOUNTANT, User.Role.DIRECTOR)
