from django.contrib import admin

from .models import Floor, Premises, PremisesType


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ("number", "name")


@admin.register(PremisesType)
class PremisesTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(Premises)
class PremisesAdmin(admin.ModelAdmin):
    list_display = ("code", "floor", "type", "area_m2", "rate_rub_m2", "status")
    list_filter = ("floor", "type", "status")
    search_fields = ("code",)
