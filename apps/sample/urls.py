from django.urls import path
from apps.dashboard.views import DashboardView

from .views import SampleView


urlpatterns = [
    # A raiz é o painel (apps/dashboard); a rota continua se chamando "index" (LOGIN_REDIRECT_URL).
    path("", DashboardView.as_view(), name="index"),
    path(
        "page_2",
        SampleView.as_view(template_name="page_2.html"),
        name="page-2",
    ),
]
