from django.urls import path

from web_project.decorators import module_required

from .views import PapelDeleteView, PapelFormView, PapelListView, PermissaoListView

urlpatterns = [
    path(
        "papeis",
        module_required("acl-papel", "auth.view_group")(PapelListView.as_view()),
        name="acl-papel-list",
    ),
    path(
        "papeis/novo",
        module_required("acl-papel", "auth.add_group")(PapelFormView.as_view()),
        name="acl-papel-create",
    ),
    path(
        "papeis/<int:pk>/editar",
        module_required("acl-papel", "auth.change_group")(PapelFormView.as_view()),
        name="acl-papel-edit",
    ),
    path(
        "papeis/<int:pk>/excluir",
        module_required("acl-papel", "auth.delete_group")(PapelDeleteView.as_view()),
        name="acl-papel-delete",
    ),
    path(
        "permissoes",
        module_required("acl-permissao", "auth.view_permission")(PermissaoListView.as_view()),
        name="acl-permissao-list",
    ),
]
