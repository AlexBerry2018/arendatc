from django.contrib import admin

from .models import Invoice, Payment, PaymentAllocation


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    readonly_fields = ("payment", "invoice", "amount", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "contract", "kind", "period", "rent_amount", "service_amount", "penalty_amount", "paid_amount", "due_date", "status")
    list_filter = ("status", "kind", "period")
    search_fields = ("number", "contract__number", "contract__tenant__legal_name")
    inlines = [AllocationInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("bank_doc_no", "paid_at", "payer_inn", "tenant", "amount", "source")
    list_filter = ("source",)
    search_fields = ("bank_doc_no", "payer_inn", "tenant__legal_name")
    inlines = [AllocationInline]
