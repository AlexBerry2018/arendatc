from django.urls import path

from . import views

app_name = "leasing"

urlpatterns = [
    path("cabinet/", views.cabinet, name="cabinet"),
    path("applications/", views.applications, name="applications"),
    path("applications/<int:pk>/<str:action>/", views.application_action, name="application_action"),
    path("contracts/", views.contracts, name="contracts"),
    path("contracts/<int:pk>/", views.contract_detail, name="contract"),
    path("contracts/<int:pk>/document/", views.contract_document, name="contract_document"),
    path("contracts/<int:pk>/<str:action>/", views.contract_action, name="contract_action"),
]
