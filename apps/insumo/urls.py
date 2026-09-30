from django.urls import path

from web_project.decorators import screen_required
from .views import InsumoView

urlpatterns = [
    path(
        "insumo",
        screen_required("insumo.view_insumo")(InsumoView.as_view(template_name="insumo.html")),
        name="insumo",
    ),
]
