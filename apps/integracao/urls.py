from django.urls import path

from web_project.decorators import module_required

from . import views

I, A = "integracoes", "agenda"
ver = module_required(I, "integracao.view_conexao")

urlpatterns = [
    path("integracoes", ver(views.IntegracoesView.as_view()), name="integracoes"),
    path("integracoes/nova/<str:provedor>", module_required(I, "integracao.add_conexao")(views.NovaConexaoView.as_view()), name="integracao-nova"),
    path("integracoes/google/iniciar", module_required(I, "integracao.add_conexao")(views.GoogleIniciarView.as_view()), name="integracao-google-iniciar"),
    path("integracoes/google/retorno", module_required(I, "integracao.add_conexao")(views.GoogleRetornoView.as_view()), name="integracao-google-retorno"),
    path("integracoes/<int:pk>", ver(views.DetalheView.as_view()), name="integracao-detalhe"),
    path("integracoes/<int:pk>/credenciais", module_required(I, "integracao.change_conexao")(views.CredenciaisView.as_view()), name="integracao-credenciais"),
    path("integracoes/<int:pk>/acao/<str:acao>", module_required(I, "integracao.change_conexao")(views.AcaoView.as_view()), name="integracao-acao"),
    path("integracoes/<int:pk>/mensagens", ver(views.MensagensView.as_view()), name="integracao-mensagens"),
    # Público: o provedor chama sem login; a ASSINATURA (e o token da URL) é que autentica.
    path("webhooks/<str:token>", views.WebhookView.as_view(), name="integracao-webhook"),
    path("agenda", module_required(A, "integracao.view_compromisso")(views.CompromissoListView.as_view()), name="compromisso-list"),
    path("agenda/novo", module_required(A, "integracao.add_compromisso")(views.CompromissoCreateView.as_view()), name="compromisso-create"),
    path("agenda/<int:pk>/editar", module_required(A, "integracao.change_compromisso")(views.CompromissoUpdateView.as_view()), name="compromisso-edit"),
]
