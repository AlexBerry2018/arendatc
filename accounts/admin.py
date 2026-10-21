from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Tenant, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (("Роль в системе", {"fields": ("role",)}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("Роль в системе", {"fields": ("role",)}),)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "inn", "ogrn", "phone", "user")
    search_fields = ("legal_name", "inn")
