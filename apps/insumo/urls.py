from django.urls import path

from web_project.decorators import module_required
from .views import InsumoView

urlpatterns = [
    path(
        "insumo",
        module_required("insumo", "insumo.view_insumo")(InsumoView.as_view(template_name="insumo.html")),
        name="insumo",
    ),
]
