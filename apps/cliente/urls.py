from django.urls import path

from web_project.decorators import screen_required

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
        screen_required("cliente.view_cliente")(ClienteListView.as_view()),
        name="cliente-list",
    ),
    path(
        "clientes/novo",
        screen_required("cliente.add_cliente")(ClienteCreateView.as_view()),
        name="cliente-create",
    ),
    path(
        "clientes/<int:pk>/editar",
        screen_required("cliente.change_cliente")(ClienteUpdateView.as_view()),
        name="cliente-edit",
    ),
    path(
        "clientes/<int:pk>/vincular-usuario",
        screen_required("cliente.change_cliente")(ClienteVincularUsuarioView.as_view()),
        name="cliente-vincular-usuario",
    ),
    path(
        "clientes/<int:pk>/desvincular-usuario/<int:user_id>",
        screen_required("cliente.change_cliente")(ClienteDesvincularUsuarioView.as_view()),
        name="cliente-desvincular-usuario",
    ),
]
