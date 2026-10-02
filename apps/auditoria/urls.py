from django.urls import path

from web_project.decorators import screen_required

from .views import AuditoriaDetailView, AuditoriaListView

# Tela administrativa e somente leitura: só a permissão de visualizar existe
# (RegistroAuditoria.Meta.default_permissions) e ela é concedida ao grupo
# Administrador por seed_acl.
urlpatterns = [
    path("auditoria", screen_required("auditoria.view_registroauditoria")(AuditoriaListView.as_view()), name="auditoria-list"),
    path(
        "auditoria/<int:pk>",
        screen_required("auditoria.view_registroauditoria")(AuditoriaDetailView.as_view()),
        name="auditoria-detail",
    ),
]
