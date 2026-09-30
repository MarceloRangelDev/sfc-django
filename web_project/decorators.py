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

Para telas que pertencem a um módulo habilitável por Cliente (Fase 3:
Insumo, Categoria, Produto, Processado, Ficha, Precificação), use
`module_required` no lugar de `screen_required` — ele faz a mesma
checagem de login + permissão e ADICIONA a checagem de que o módulo está
habilitado para o Cliente do usuário logado:

    from web_project.decorators import module_required

    urlpatterns = [
        path("insumo", module_required("insumo", "insumo.view_insumo")(InsumoView.as_view(...)), name="insumo"),
    ]

Assim como a permissão, o módulo é checado no backend — não é só uma
questão de esconder o item do menu (ver filtro `has_menu_access` em
web_project/template_tags/theme.py): acessar a URL direto com o módulo
desabilitado também cai na página "Não autorizado".
"""
from functools import wraps

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def screen_required(permission, raise_exception=True):
    """Exige login E a permissão informada para acessar a view."""

    def decorator(view):
        protected = permission_required(permission, raise_exception=raise_exception)(view)
        return login_required(protected)

    return decorator


def modulo_habilitado_para(user, modulo_slug):
    """Retorna True se o Cliente do usuário logado tem o módulo
    `modulo_slug` habilitado (e o módulo está disponível no sistema).
    Reaproveitado tanto pelo decorator `module_required` (backend) quanto
    pelo filtro de template `has_module`/`has_menu_access` (menu)."""
    if not user.is_authenticated:
        return False

    # Import local para evitar import circular (apps.modulo depende de
    # apps.cliente, e este módulo é importado bem cedo, junto com as urls).
    from apps.modulo.models import ClienteModulo

    cliente = getattr(getattr(user, "profile", None), "cliente", None)
    if cliente is None:
        return False

    return ClienteModulo.objects.filter(
        cliente=cliente, modulo__slug=modulo_slug, modulo__ativo=True, ativo=True
    ).exists()


def module_required(modulo_slug, permission, raise_exception=True):
    """Exige login, a permissão informada E que o módulo `modulo_slug`
    esteja habilitado para o Cliente do usuário logado."""

    def decorator(view):
        protected = screen_required(permission, raise_exception=raise_exception)(view)

        @wraps(protected)
        def wrapped(request, *args, **kwargs):
            if request.user.is_authenticated and not modulo_habilitado_para(request.user, modulo_slug):
                if raise_exception:
                    raise PermissionDenied("Este módulo não está habilitado para o seu cliente.")
                return redirect("pages-misc-not-authorized")
            return protected(request, *args, **kwargs)

        return wrapped

    return decorator
