from django.urls import path

from web_project.decorators import module_required

from .views import (
    ClienteCreateView,
    ClienteDesvincularUsuarioView,
    ClienteListView,
    ClienteUpdateView,
    ClienteVincularUsuarioView,
)

urlpatterns = [
    path(
        "clientes",
        module_required("cliente", "cliente.view_cliente")(ClienteListView.as_view()),
        name="cliente-list",
    ),
    path(
        "clientes/novo",
        module_required("cliente", "cliente.add_cliente")(ClienteCreateView.as_view()),
        name="cliente-create",
    ),
    path(
        "clientes/<int:pk>/editar",
        module_required("cliente", "cliente.change_cliente")(ClienteUpdateView.as_view()),
        name="cliente-edit",
    ),
    path(
        "clientes/<int:pk>/vincular-usuario",
        module_required("cliente", "cliente.change_cliente")(ClienteVincularUsuarioView.as_view()),
        name="cliente-vincular-usuario",
    ),
    path(
        "clientes/<int:pk>/desvincular-usuario/<int:user_id>",
        module_required("cliente", "cliente.change_cliente")(ClienteDesvincularUsuarioView.as_view()),
        name="cliente-desvincular-usuario",
    ),
]
