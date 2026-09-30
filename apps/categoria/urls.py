from django.urls import path

from web_project.decorators import module_required

from .views import CategoriaCreateView, CategoriaListView, CategoriaUpdateView

urlpatterns = [
    path(
        "categoria",
        module_required("categoria", "categoria.view_categoria")(CategoriaListView.as_view()),
        name="categoria",
    ),
    path(
        "categoria/novo",
        module_required("categoria", "categoria.add_categoria")(CategoriaCreateView.as_view()),
        name="categoria-create",
    ),
    path(
        "categoria/<int:pk>/editar",
        module_required("categoria", "categoria.change_categoria")(CategoriaUpdateView.as_view()),
        name="categoria-edit",
    ),
]
