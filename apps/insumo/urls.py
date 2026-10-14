from django.urls import path

from web_project.decorators import module_required

from .views import InsumoCreateView, InsumoExcluirView, InsumoHistoricoView, InsumoListView, InsumoPorUuidView, InsumoUpdateView

urlpatterns = [
    path(
        "insumo/<int:pk>/historico",
        module_required("insumo", "insumo.view_insumo")(InsumoHistoricoView.as_view()),
        name="insumo-historico",
    ),
    # Destino do QR Code das etiquetas (ver InsumoPorUuidView): login + estoque habilitado + mesmo tenant.
    path(
        "i/<uuid:uuid>",
        module_required("estoque", "estoque.view_movimentoestoque")(InsumoPorUuidView.as_view()),
        name="insumo-uuid",
    ),
    path(
        "insumo",
        module_required("insumo", "insumo.view_insumo")(InsumoListView.as_view()),
        name="insumo",
    ),
    path(
        "insumo/novo",
        module_required("insumo", "insumo.add_insumo")(InsumoCreateView.as_view()),
        name="insumo-create",
    ),
    path(
        "insumo/<int:pk>/editar",
        module_required("insumo", "insumo.change_insumo")(InsumoUpdateView.as_view()),
        name="insumo-edit",
    ),
    path(
        "insumo/<int:pk>/excluir",
        module_required("insumo", "insumo.delete_insumo")(InsumoExcluirView.as_view()),
        name="insumo-excluir",
    ),
]
