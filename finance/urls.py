from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.finance_home, name="home"),
    path("generate/", views.generate, name="generate"),
    path("penalties/", views.penalties, name="penalties"),
    path("import/", views.import_statement, name="import"),
    path("payments/<int:pk>/assign/", views.assign, name="assign"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
]
