from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("premises", views.PremisesViewSet, basename="premises")
router.register("applications", views.ApplicationViewSet, basename="application")
router.register("contracts", views.ContractViewSet, basename="contract")
router.register("invoices", views.InvoiceViewSet, basename="invoice")

urlpatterns = [
    path("", include(router.urls)),
    path("payments/import/", views.payments_import, name="payments-import"),
    path("reports/occupancy/", views.report_occupancy, name="report-occupancy"),
    path("reports/debt/", views.report_debt, name="report-debt"),
]
