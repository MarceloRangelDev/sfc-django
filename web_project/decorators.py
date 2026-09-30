"""
Helpers para proteger telas (views) usando o sistema nativo de
Grupos + Permissions do Django (o mesmo consultado pelo filtro de template
`has_permission`, usado no menu lateral - ver
templates/layout/partials/menu/vertical/partials/*.html).

Uso típico em urls.py, no mesmo estilo que já era usado com `login_required`:

    from web_project.decorators import screen_required

    urlpatterns = [
        path("insumo", screen_required("insumo.view_insumo")(InsumoView.as_view(...)), name="insumo"),
    ]

Para telas de CRUD (quando forem criadas), use a permissão correspondente:
  - visualizar -> "<app>.view_<model>"
  - criar      -> "<app>.add_<model>"
  - editar     -> "<app>.change_<model>"
  - excluir    -> "<app>.delete_<model>"

Usuários sem a permissão são redirecionados para a página "Não autorizado"
(handler403, configurado em config/urls.py).
"""
from django.contrib.auth.decorators import login_required, permission_required


def screen_required(permission, raise_exception=True):
    """Exige login E a permissão informada para acessar a view."""

    def decorator(view):
        protected = permission_required(permission, raise_exception=raise_exception)(view)
        return login_required(protected)

    return decorator
