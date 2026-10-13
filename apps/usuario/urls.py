from django.urls import path

from web_project.decorators import module_required

from .views import UsuarioAlternarAtivoView, UsuarioFormView, UsuarioListView

# Usa as permissões nativas do Django sobre auth.User. A exclusão física
# não existe de propósito: usuários são apenas inativados (preserva histórico
# e auditoria), então não há rota nem permissão "delete".
urlpatterns = [
    path("usuarios", module_required("usuario", "auth.view_user")(UsuarioListView.as_view()), name="usuario-list"),
    path("usuarios/novo", module_required("usuario", "auth.add_user")(UsuarioFormView.as_view()), name="usuario-create"),
    path("usuarios/<int:pk>/editar", module_required("usuario", "auth.change_user")(UsuarioFormView.as_view()), name="usuario-edit"),
    path(
        "usuarios/<int:pk>/alternar-ativo",
        module_required("usuario", "auth.change_user")(UsuarioAlternarAtivoView.as_view()),
        name="usuario-alternar-ativo",
    ),
]
