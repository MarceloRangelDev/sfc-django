from django.urls import path

from web_project.decorators import module_required

from . import views

M = "orcamento"
v = lambda perm, view: module_required(M, f"orcamento.{perm}")(view.as_view())  # noqa: E731

urlpatterns = [
    path("orcamentos", v("view_orcamento", views.OrcamentoListView), name="orcamento-list"),
    path("orcamentos/novo", v("add_orcamento", views.OrcamentoFormView), name="orcamento-create"),
    path("orcamentos/calcular", v("view_orcamento", views.OrcamentoCalcularView), name="orcamento-calcular"),
    path("orcamentos/<int:pk>", v("view_orcamento", views.OrcamentoDetalheView), name="orcamento-detalhe"),
    path("orcamentos/<int:pk>/editar", v("change_orcamento", views.OrcamentoFormView), name="orcamento-edit"),
    path("orcamentos/<int:pk>/pdf", v("view_orcamento", views.OrcamentoPdfView), name="orcamento-pdf"),
    # "Enviar / imprimir": ver a página e gerar PDF só exige ver; e-mail/WhatsApp conferem `enviar_orcamento` no serviço.
    path("orcamentos/<int:pk>/enviar", v("view_orcamento", views.OrcamentoEnviarView), name="orcamento-enviar"),
    path("orcamentos/<int:pk>/<str:acao>", v("view_orcamento", views.OrcamentoAcaoView), name="orcamento-acao"),
    # Público (sem login): o token longo do link do PDF enviado por WhatsApp é a autorização.
    path("orcamento/publico/<str:token>", views.PdfPublicoView.as_view(), name="orcamento-publico"),
]
