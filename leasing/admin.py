from django.contrib import admin

from .models import Application, Booking, Contract, ContractVersion


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "premises", "lease_from", "lease_to", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("tenant__legal_name", "premises__code")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("premises", "lease_from", "lease_to", "expires_at", "is_active")
    list_filter = ("is_active",)


class ContractVersionInline(admin.TabularInline):
    model = ContractVersion
    extra = 0
    fields = ("version_no", "sha256", "created_by", "created_at")
    readonly_fields = fields


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ("number", "tenant", "premises", "date_from", "date_to", "status")
    list_filter = ("status",)
    search_fields = ("number", "tenant__legal_name", "premises__code")
    inlines = [ContractVersionInline]
