from django.urls import path

from web_project.decorators import module_required

from .views import ProcessadoCalcularView, ProcessadoCreateView, ProcessadoExcluirView, ProcessadoListView, ProcessadoUpdateView

urlpatterns = [
    path(
        "processado",
        module_required("processado", "processado.view_processado")(ProcessadoListView.as_view()),
        name="processado",
    ),
    path(
        "processado/novo",
        module_required("processado", "processado.add_processado")(ProcessadoCreateView.as_view()),
        name="processado-create",
    ),
    # Prévia dos custos da ficha técnica do formulário (só lê e calcula).
    path(
        "processado/calcular",
        module_required("processado", "processado.view_processado")(ProcessadoCalcularView.as_view()),
        name="processado-calcular",
    ),
    path(
        "processado/<int:pk>/editar",
        module_required("processado", "processado.change_processado")(ProcessadoUpdateView.as_view()),
        name="processado-edit",
    ),
    path(
        "processado/<int:pk>/excluir",
        module_required("processado", "processado.delete_processado")(ProcessadoExcluirView.as_view()),
        name="processado-excluir",
    ),
]
