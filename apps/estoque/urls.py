from django.urls import path

from web_project.decorators import module_required

from . import views

# Dois grupos de permissão: estoque.*_movimentoestoque (saldos, movimentos, mínimos) e
# estoque.*_transferenciaestoque (transferências entre unidades; concedida só a quem for autorizado).
ver = module_required("estoque", "estoque.view_movimentoestoque")
urlpatterns = [
    path("estoque", ver(views.EstoqueListView.as_view()), name="estoque"),
    path("estoque/movimentar", module_required("estoque", "estoque.add_movimentoestoque")(views.MovimentarView.as_view()), name="estoque-movimentar"),
    path("estoque/movimentos", ver(views.MovimentosListView.as_view()), name="estoque-movimentos"),
    path("estoque/movimentos/<int:pk>/editar", module_required("estoque", "estoque.change_movimentoestoque")(views.MovimentoEditarView.as_view()), name="estoque-movimento-editar"),
    path("estoque/etiquetas", ver(views.EtiquetaView.as_view()), name="estoque-etiquetas"),
    path("estoque/alertas", ver(views.AlertasView.as_view()), name="estoque-alertas"),
    path("estoque/insumo/<int:pk>", ver(views.EstoqueInsumoView.as_view()), name="estoque-insumo"),
    path("estoque/insumo/<int:pk>/minimo", module_required("estoque", "estoque.change_movimentoestoque")(views.MinimoView.as_view()), name="estoque-minimo"),
    path("estoque/transferencias", module_required("estoque", "estoque.view_transferenciaestoque")(views.TransferenciasListView.as_view()), name="estoque-transferencias"),
    path("estoque/transferencias/nova", module_required("estoque", "estoque.add_transferenciaestoque")(views.TransferenciaCreateView.as_view()), name="estoque-transferencia-nova"),
    path("estoque/transferencias/<int:pk>", module_required("estoque", "estoque.view_transferenciaestoque")(views.TransferenciaDetalheView.as_view()), name="estoque-transferencia"),
]
