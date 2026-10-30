from django.urls import path

from web_project.decorators import module_required

from . import views, views_funcao

M = "mao-de-obra"
v = lambda p, view: module_required(M, f"maodeobra.{p}")(view.as_view())  # noqa: E731

urlpatterns = [
    path("mao-de-obra", v("view_apontamento", views.ApontamentoListView), name="apontamento-list"),
    path("mao-de-obra/novo", v("add_apontamento", views.ApontamentoCreateView), name="apontamento-create"),
    path("mao-de-obra/<int:pk>/editar", v("change_apontamento", views.ApontamentoUpdateView), name="apontamento-edit"),
    path("mao-de-obra/<int:pk>/excluir", v("delete_apontamento", views.ApontamentoExcluirView), name="apontamento-excluir"),
    path("mao-de-obra/relatorio", v("view_apontamento", views.RelatorioView), name="mao-obra-relatorio"),
    path("mao-de-obra/regras", v("change_configuracaomaodeobra", views.ConfiguracaoView), name="mao-obra-config"),
    # Serviços (produto do tipo serviço): módulo Mão de obra + permissões do cadastro de produto.
    path("mao-de-obra/servicos", module_required(M, "produto.view_produto")(views.ServicoListView.as_view()), name="servico-list"),
    path("mao-de-obra/servicos/novo", module_required(M, "produto.add_produto")(views.ServicoCreateView.as_view()), name="servico-create"),
    path("mao-de-obra/servicos/<int:pk>/editar", module_required(M, "produto.change_produto")(views.ServicoUpdateView.as_view()), name="servico-edit"),
    path("mao-de-obra/servicos/<int:pk>/excluir", module_required(M, "produto.delete_produto")(views.ServicoExcluirView.as_view()), name="servico-excluir"),
    path("mao-de-obra/colaboradores", v("view_colaborador", views.ColaboradorListView), name="colaborador-list"),
    path("mao-de-obra/colaboradores/novo", v("add_colaborador", views.ColaboradorCreateView), name="colaborador-create"),
    path("mao-de-obra/colaboradores/<int:pk>/editar", v("change_colaborador", views.ColaboradorUpdateView), name="colaborador-edit"),
    path("mao-de-obra/colaboradores/<int:pk>/excluir", v("delete_colaborador", views.ColaboradorExcluirView), name="colaborador-excluir"),
    # Colaborador: composição do custo (herdado × personalizado) por função.
    path("mao-de-obra/colaboradores/<int:pk>/composicao", v("view_colaborador", views_funcao.ColaboradorComposicaoView), name="colaborador-composicao"),
    # Funções de mão de obra e seus itens de custo (salário, adicionais, DSR, encargos, provisões, benefícios, indiretos).
    path("mao-de-obra/funcoes", v("view_funcaomaodeobra", views_funcao.FuncaoListView), name="funcao-list"),
    path("mao-de-obra/funcoes/nova", v("add_funcaomaodeobra", views_funcao.FuncaoCreateView), name="funcao-create"),
    path("mao-de-obra/funcoes/<int:pk>", v("view_funcaomaodeobra", views_funcao.FuncaoDetalheView), name="funcao-detalhe"),
    path("mao-de-obra/funcoes/<int:pk>/editar", v("change_funcaomaodeobra", views_funcao.FuncaoUpdateView), name="funcao-edit"),
    path("mao-de-obra/funcoes/<int:pk>/excluir", v("delete_funcaomaodeobra", views_funcao.FuncaoExcluirView), name="funcao-excluir"),
    path("mao-de-obra/funcoes/<int:pk>/itens/novo", v("add_componentefuncao", views_funcao.ComponenteCreateView), name="componente-create"),
    path("mao-de-obra/itens/<int:pk>/editar", v("change_componentefuncao", views_funcao.ComponenteUpdateView), name="componente-edit"),
    path("mao-de-obra/itens/<int:pk>/excluir", v("delete_componentefuncao", views_funcao.ComponenteExcluirView), name="componente-excluir"),
    # Planejamento e indicadores.
    path("mao-de-obra/simulacoes", v("view_simulacaopromocao", views_funcao.SimulacaoListView), name="simulacao-list"),
    path("mao-de-obra/simulacoes/nova", v("add_simulacaopromocao", views_funcao.SimulacaoCreateView), name="simulacao-create"),
    path("mao-de-obra/simulacoes/<int:pk>", v("view_simulacaopromocao", views_funcao.SimulacaoDetalheView), name="simulacao-detalhe"),
    path("mao-de-obra/simulacoes/<int:pk>/editar", v("change_simulacaopromocao", views_funcao.SimulacaoUpdateView), name="simulacao-edit"),
    path("mao-de-obra/simulacoes/<int:pk>/excluir", v("delete_simulacaopromocao", views_funcao.SimulacaoExcluirView), name="simulacao-excluir"),
    path("mao-de-obra/dashboard", v("view_colaborador", views_funcao.DashboardView), name="mao-obra-dashboard"),
]
