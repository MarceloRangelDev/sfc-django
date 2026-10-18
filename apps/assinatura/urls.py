from django.urls import path

from web_project.decorators import module_required

from . import views

A = "assinatura"

urlpatterns = [
    path("assinatura/", module_required(A, "assinatura.view_assinatura")(views.AssinaturaView.as_view()), name="assinatura"),
    path("assinatura/assinar", module_required(A, "assinatura.gerenciar_assinatura")(views.AssinarView.as_view()), name="assinatura-assinar"),
    path("assinatura/cancelar", module_required(A, "assinatura.gerenciar_assinatura")(views.CancelarView.as_view()), name="assinatura-cancelar"),
    # Público (sem login): autenticado pelo segredo na URL; ver views.WebhookView.
    path("assinatura/webhook/<str:token>/", views.WebhookView.as_view(), name="assinatura-webhook"),
]
