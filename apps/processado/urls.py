from django.urls import path

from web_project.decorators import screen_required
from .views import ProcessadoView

urlpatterns = [
    path(
        "processado",
        screen_required("processado.view_processado")(ProcessadoView.as_view(template_name="processado.html")),
        name="processado",
    ),
]
