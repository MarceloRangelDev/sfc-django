from django.urls import path

from web_project.decorators import module_required
from .views import ProcessadoView

urlpatterns = [
    path(
        "processado",
        module_required("processado", "processado.view_processado")(ProcessadoView.as_view(template_name="processado.html")),
        name="processado",
    ),
]
