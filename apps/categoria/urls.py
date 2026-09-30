from django.urls import path

from web_project.decorators import module_required
from .views import CategoriaView

urlpatterns = [
    path(
        "categoria",
        module_required("categoria", "categoria.view_categoria")(CategoriaView.as_view(template_name="categoria.html")),
        name="categoria",
    ),
]
