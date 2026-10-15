from django.urls import path

from web_project.decorators import module_required

from . import views
from .models import CotacaoItem, CotacaoLoja, CotacaoPreco

M = "cotacao"
ver = module_required(M, "cotacao.view_cotacao")
alterar = module_required(M, "cotacao.change_cotacao")

urlpatterns = [
    path("cotacao", ver(views.CotacaoListView.as_view()), name="cotacao"),
    path("cotacao/nova", module_required(M, "cotacao.add_cotacao")(views.CotacaoCreateView.as_view()), name="cotacao-create"),
    path("cotacao/<int:pk>", ver(views.CotacaoDetailView.as_view()), name="cotacao-detail"),
    path("cotacao/<int:pk>/editar", alterar(views.CotacaoUpdateView.as_view()), name="cotacao-edit"),
    path("cotacao/<int:pk>/excluir", module_required(M, "cotacao.delete_cotacao")(views.CotacaoExcluirView.as_view()), name="cotacao-excluir"),
    path("cotacao/<int:pk>/status", alterar(views.StatusView.as_view()), name="cotacao-status"),
    path("cotacao/<int:pk>/exportar", ver(views.ExportarView.as_view()), name="cotacao-exportar"),
    path("cotacao/<int:pk>/importar-lista", alterar(views.ImportarListaView.as_view()), name="cotacao-importar"),
    path("cotacao/<int:pk>/gerar-contas", alterar(views.GerarContasView.as_view()), name="cotacao-contas"),
    # lojas
    path("cotacao/<int:pk>/lojas/nova", alterar(views.LojaFormView.as_view()), name="cotacao-loja-create"),
    path("cotacao/<int:pk>/lojas/<int:loja>/editar", alterar(views.LojaFormView.as_view()), name="cotacao-loja-edit"),
    path("cotacao/<int:pk>/lojas/<int:loja>/excluir", alterar(views.ExcluirFilhoView.as_view(modelo=CotacaoLoja, campo_pk="loja")), name="cotacao-loja-excluir"),
    # itens
    path("cotacao/<int:pk>/itens/novo", alterar(views.ItemFormView.as_view()), name="cotacao-item-create"),
    path("cotacao/<int:pk>/itens/<int:item>/editar", alterar(views.ItemFormView.as_view()), name="cotacao-item-edit"),
    path("cotacao/<int:pk>/itens/<int:item>/excluir", alterar(views.ExcluirFilhoView.as_view(modelo=CotacaoItem, campo_pk="item")), name="cotacao-item-excluir"),
    path("cotacao/<int:pk>/itens/<int:item>/limpar-escolha", alterar(views.LimparEscolhaView.as_view()), name="cotacao-limpar-escolha"),
    # preços (ofertas das lojas)
    path("cotacao/<int:pk>/itens/<int:item>/precos/novo", alterar(views.PrecoFormView.as_view()), name="cotacao-preco-create"),
    path("cotacao/<int:pk>/precos/<int:preco>/editar", alterar(views.PrecoFormView.as_view()), name="cotacao-preco-edit"),
    path("cotacao/<int:pk>/precos/<int:preco>/excluir", alterar(views.ExcluirFilhoView.as_view(modelo=CotacaoPreco, campo_pk="preco")), name="cotacao-preco-excluir"),
    path("cotacao/<int:pk>/precos/<int:preco>/escolher", alterar(views.EscolherView.as_view()), name="cotacao-preco-escolher"),
]
