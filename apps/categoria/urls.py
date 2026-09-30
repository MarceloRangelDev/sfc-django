from django.urls import path

from web_project.decorators import screen_required
from .views import CategoriaView

urlpatterns = [
    path(
        "categoria",
        screen_required("categoria.view_categoria")(CategoriaView.as_view(template_name="categoria.html")),
        name="categoria",
    ),
]
