from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from core import views as core_views

admin.site.site_header = "АрендаТЦ — администрирование"
admin.site.site_title = "АрендаТЦ"

urlpatterns = [
    path("", core_views.home, name="home"),
    path("dashboard/", core_views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/", include("accounts.urls")),
    path("catalog/", include("catalog.urls")),
    path("leasing/", include("leasing.urls")),
    path("finance/", include("finance.urls")),
    path("api/", include("api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    path("admin/", admin.site.urls),
]
