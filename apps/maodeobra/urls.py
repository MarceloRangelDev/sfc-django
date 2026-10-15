from django.urls import path

from web_project.decorators import module_required

from . import views

M = "mao-de-obra"
v = lambda p, view: module_required(M, f"maodeobra.{p}")(view.as_view())  # noqa: E731

urlpatterns = [
    path("mao-de-obra", v("view_apontamento", views.ApontamentoListView), name="apontamento-list"),
    path("mao-de-obra/novo", v("add_apontamento", views.ApontamentoCreateView), name="apontamento-create"),
    path("mao-de-obra/<int:pk>/editar", v("change_apontamento", views.ApontamentoUpdateView), name="apontamento-edit"),
    path("mao-de-obra/<int:pk>/excluir", v("delete_apontamento", views.ApontamentoExcluirView), name="apontamento-excluir"),
    path("mao-de-obra/relatorio", v("view_apontamento", views.RelatorioView), name="mao-obra-relatorio"),
    path("mao-de-obra/regras", v("change_configuracaomaodeobra", views.ConfiguracaoView), name="mao-obra-config"),
    path("mao-de-obra/colaboradores", v("view_colaborador", views.ColaboradorListView), name="colaborador-list"),
    path("mao-de-obra/colaboradores/novo", v("add_colaborador", views.ColaboradorCreateView), name="colaborador-create"),
    path("mao-de-obra/colaboradores/<int:pk>/editar", v("change_colaborador", views.ColaboradorUpdateView), name="colaborador-edit"),
    path("mao-de-obra/colaboradores/<int:pk>/excluir", v("delete_colaborador", views.ColaboradorExcluirView), name="colaborador-excluir"),
]
