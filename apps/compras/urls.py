from django.urls import path

from web_project.decorators import module_required

from . import views

M = "compras"

urlpatterns = [
    path("compras", module_required(M, "compras.view_itemcompra")(views.ComprasListView.as_view()), name="compras"),
    path("compras/novo", module_required(M, "compras.add_itemcompra")(views.ItemCreateView.as_view()), name="compras-create"),
    path("compras/sugestoes/adicionar", module_required(M, "compras.add_itemcompra")(views.AdicionarSugestoesView.as_view()), name="compras-sugestoes-adicionar"),
    path("compras/<int:pk>/editar", module_required(M, "compras.change_itemcompra")(views.ItemUpdateView.as_view()), name="compras-edit"),
    path("compras/<int:pk>/excluir", module_required(M, "compras.delete_itemcompra")(views.ItemExcluirView.as_view()), name="compras-excluir"),
]
