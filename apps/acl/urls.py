from django.urls import path

from web_project.decorators import screen_required

from .views import PapelDeleteView, PapelFormView, PapelListView, PermissaoListView

urlpatterns = [
    path(
        "papeis",
        screen_required("auth.view_group")(PapelListView.as_view()),
        name="acl-papel-list",
    ),
    path(
        "papeis/novo",
        screen_required("auth.add_group")(PapelFormView.as_view()),
        name="acl-papel-create",
    ),
    path(
        "papeis/<int:pk>/editar",
        screen_required("auth.change_group")(PapelFormView.as_view()),
        name="acl-papel-edit",
    ),
    path(
        "papeis/<int:pk>/excluir",
        screen_required("auth.delete_group")(PapelDeleteView.as_view()),
        name="acl-papel-delete",
    ),
    path(
        "permissoes",
        screen_required("auth.view_permission")(PermissaoListView.as_view()),
        name="acl-permissao-list",
    ),
]
