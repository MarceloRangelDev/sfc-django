from django.urls import path

from web_project.decorators import screen_required

from .views import UsuarioAlternarAtivoView, UsuarioFormView, UsuarioListView

# Usa as permissões nativas do Django sobre auth.User. A exclusão física
# não existe de propósito: usuários são apenas inativados (preserva histórico
# e auditoria), então não há rota nem permissão "delete".
urlpatterns = [
    path("usuarios", screen_required("auth.view_user")(UsuarioListView.as_view()), name="usuario-list"),
    path("usuarios/novo", screen_required("auth.add_user")(UsuarioFormView.as_view()), name="usuario-create"),
    path("usuarios/<int:pk>/editar", screen_required("auth.change_user")(UsuarioFormView.as_view()), name="usuario-edit"),
    path(
        "usuarios/<int:pk>/alternar-ativo",
        screen_required("auth.change_user")(UsuarioAlternarAtivoView.as_view()),
        name="usuario-alternar-ativo",
    ),
]
