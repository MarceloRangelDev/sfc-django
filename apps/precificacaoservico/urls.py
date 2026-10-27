from django.urls import path

from web_project.decorators import module_required

from . import views

M = "precificacao-servico"
v = lambda perm, view: module_required(M, f"precificacaoservico.{perm}")(view.as_view())  # noqa: E731

urlpatterns = [
    path("precificacao-servico", v("view_proposta", views.PropostaListView), name="ps-proposta-list"),
    path("precificacao-servico/propostas/nova", v("add_proposta", views.PropostaFormView), name="ps-proposta-create"),
    path("precificacao-servico/propostas/calcular", v("view_proposta", views.PropostaCalcularView), name="ps-proposta-calcular"),
    path("precificacao-servico/propostas/<int:pk>", v("view_proposta", views.PropostaDetalheView), name="ps-proposta-detalhe"),
    path("precificacao-servico/propostas/<int:pk>/editar", v("change_proposta", views.PropostaFormView), name="ps-proposta-edit"),
    path("precificacao-servico/propostas/<int:pk>/imprimir", v("view_proposta", views.PropostaImprimirView), name="ps-proposta-imprimir"),
    path("precificacao-servico/propostas/<int:pk>/<str:tipo>/<int:registro_id>/excluir", v("change_proposta", views.RegistroExcluirView), name="ps-registro-excluir"),
    path("precificacao-servico/propostas/<int:pk>/<str:acao>", v("view_proposta", views.PropostaAcaoView), name="ps-proposta-acao"),
    path("precificacao-servico/configuracao", v("view_configuracaoservico", views.ConfiguracaoView), name="ps-config"),
    path("precificacao-servico/despesas", v("view_despesafixa", views.DespesaListView), name="ps-despesa-list"),
    path("precificacao-servico/despesas/nova", v("add_despesafixa", views.DespesaCreateView), name="ps-despesa-create"),
    path("precificacao-servico/despesas/<int:pk>/editar", v("change_despesafixa", views.DespesaUpdateView), name="ps-despesa-edit"),
    path("precificacao-servico/despesas/<int:pk>/excluir", v("delete_despesafixa", views.DespesaExcluirView), name="ps-despesa-excluir"),
    path("precificacao-servico/modelos", v("view_modeloservico", views.ModeloListView), name="ps-modelo-list"),
    path("precificacao-servico/modelos/novo", v("add_modeloservico", views.ModeloCreateView), name="ps-modelo-create"),
    path("precificacao-servico/modelos/<int:pk>/editar", v("change_modeloservico", views.ModeloUpdateView), name="ps-modelo-edit"),
    path("precificacao-servico/modelos/<int:pk>/excluir", v("delete_modeloservico", views.ModeloExcluirView), name="ps-modelo-excluir"),
]
