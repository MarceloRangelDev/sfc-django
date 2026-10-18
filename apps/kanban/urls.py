from django.urls import path

from web_project.decorators import module_required

from . import views

K = "kanban"

urlpatterns = [
    path("kanban", module_required(K, "kanban.view_cartao")(views.KanbanView.as_view()), name="kanban"),
    path("kanban/colunas/nova", module_required(K, "kanban.add_coluna")(views.ColunaCriar.as_view()), name="kanban-coluna-criar"),
    path("kanban/colunas/<int:pk>/renomear", module_required(K, "kanban.change_coluna")(views.ColunaRenomear.as_view()), name="kanban-coluna-renomear"),
    path("kanban/colunas/<int:pk>/excluir", module_required(K, "kanban.delete_coluna")(views.ColunaExcluir.as_view()), name="kanban-coluna-excluir"),
    path("kanban/cartoes/novo", module_required(K, "kanban.add_cartao")(views.CartaoCriar.as_view()), name="kanban-cartao-criar"),
    path("kanban/cartoes/<int:pk>/editar", module_required(K, "kanban.change_cartao")(views.CartaoEditar.as_view()), name="kanban-cartao-editar"),
    path("kanban/cartoes/<int:pk>/excluir", module_required(K, "kanban.delete_cartao")(views.CartaoExcluir.as_view()), name="kanban-cartao-excluir"),
    path("kanban/cartoes/<int:pk>/mover", module_required(K, "kanban.mover_cartao")(views.CartaoMover.as_view()), name="kanban-cartao-mover"),
]
