from django.utils.safestring import mark_safe
from django import template
from web_project.template_helpers.theme import TemplateHelper
from django.contrib.auth.decorators import user_passes_test

register = template.Library()


# Register tags as an adapter for the Theme class usage in the HTML template


@register.simple_tag
def get_theme_variables(scope):
    return mark_safe(TemplateHelper.get_theme_variables(scope))


@register.simple_tag
def get_theme_config(scope):
    return mark_safe(TemplateHelper.get_theme_config(scope))


def _item_ativo(item, request):
    """Item do menu que corresponde à página atual. Itens do catálogo de
    módulos têm `slug` e são casados com o módulo da rota (request.modulo_slug,
    definido por module_required), então telas internas (novo/editar) também
    marcam o item. Itens estáticos do JSON casam pelo nome da URL."""
    modulo_atual = getattr(request, "modulo_slug", None)
    if modulo_atual and item.get("slug") == modulo_atual:
        return True
    destino = item.get("url")
    rota_atual = getattr(getattr(request, "resolver_match", None), "url_name", None)  # None em páginas de erro
    return bool(destino) and (destino == request.path or destino == rota_atual)


@register.filter
def menu_item_ativo(item, request):
    return _item_ativo(item, request)


@register.filter
def filter_by_url(submenu, request):
    """True se algum item (em qualquer nível) do submenu é a página atual."""
    return any(
        _item_ativo(subitem, request) or filter_by_url(subitem.get("submenu"), request)
        for subitem in submenu or []
    )


# Check if the user has the group
@register.filter
def has_group(user, group):
    if user.groups.filter(name=group).exists():
        return True

# Check if the user has the permission
@register.filter
def has_permission(user, permission):
    if user.has_perm(permission):
        return True


# Verifica se o módulo (Fase 3) está habilitado para o Cliente do usuário.
@register.filter
def has_module(user, modulo_slug):
    from web_project.decorators import modulo_habilitado_para

    return modulo_habilitado_para(user, modulo_slug)


# Usado pelo menu lateral/superior (menu_link_template.html e
# menu_collapsible_template.html): um item só aparece se o usuário tiver a
# permissão do item (quando houver) E, se o item pertencer a um módulo de
# negócio, se esse módulo estiver habilitado para o Cliente do usuário.
# Itens sem "permission" nem "module" continuam sempre visíveis (ex: "Meu
# Perfil", páginas de erro) — mesmo comportamento de antes da Fase 3.
@register.filter
def has_menu_access(user, item):
    permission = item.get("permission")
    if permission and not has_permission(user, permission):
        return False

    modulo_slug = item.get("module")
    if modulo_slug and not has_module(user, modulo_slug):
        return False

    return True


# For checking if the user group is admin
@register.filter(name="is_admin")
def is_admin(user):
    return user.groups.filter(name="admin").exists()

@register.filter(name="admin_required")
def admin_required(view_func):
    return user_passes_test(is_admin, login_url='login')(view_func)


# For checking if the user group is client
@register.filter(name="is_client")
def is_client(user):
    return user.groups.filter(name="client").exists()

@register.filter(name="client_required")
def client_required(view_func):
    return user_passes_test(is_client, login_url='login')(view_func)


# For checking if is_superuser
@register.filter(name="is_superuser")
def is_superuser(user):
    return user.is_superuser

@register.filter(name="superuser_required")
def superuser_required(view_func):
    return user_passes_test(is_superuser, login_url='login')(view_func)


# For checking if is_staff
@register.filter(name="is_staff")
def is_staff(user):
    return user.is_staff

@register.filter(name="staff_required")
def staff_required(view_func):
    return user_passes_test(is_staff, login_url='login')(view_func)


# Segmento de trabalho (Serviço x Produto) do perfil do usuário logado.
# Usado para aplicar regras diferentes conforme o segmento (ex: na Precificação).
@register.filter(name="is_segmento_servico")
def is_segmento_servico(user):
    return user.is_authenticated and getattr(user.profile, "precifica_servico", False)

@register.filter(name="is_segmento_produto")
def is_segmento_produto(user):
    return user.is_authenticated and getattr(user.profile, "precifica_produto", False)


@register.simple_tag
def current_url(request):
    return request.build_absolute_uri()
