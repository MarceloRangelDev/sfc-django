from django.urls import path
from webpush.views import ServiceWorkerView

from web_project.decorators import module_required

from .views import (
    AbrirNotificacaoView,
    HistoricoView,
    InscricaoPushView,
    MinhasNotificacoesView,
    NovaNotificacaoView,
    ResumoNotificacoesView,
)

# Enviar e consultar o histórico exigem o módulo "notificacao" + permissão.
# A caixa de entrada pessoal (minhas/abrir/inscrição push) só exige login:
# cada usuário só alcança as próprias notificações.
urlpatterns = [
    path("notificacoes", module_required("notificacao", "notificacao.view_notificacao")(HistoricoView.as_view()), name="notificacao-list"),
    path("notificacoes/nova", module_required("notificacao", "notificacao.add_notificacao")(NovaNotificacaoView.as_view()), name="notificacao-create"),
    path("notificacoes/minhas", MinhasNotificacoesView.as_view(), name="notificacao-minhas"),
    path("notificacoes/resumo", ResumoNotificacoesView.as_view(), name="notificacao-resumo"),
    path("notificacoes/<int:pk>/abrir", AbrirNotificacaoView.as_view(), name="notificacao-abrir"),
    # Service worker do web push (precisa ser servido do mesmo domínio, na raiz).
    path("service-worker.js", ServiceWorkerView.as_view(), name="service-worker"),
    path("notificacoes/inscricao-push", InscricaoPushView.as_view(), name="notificacao-inscricao-push"),
]
