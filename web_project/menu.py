"""Menu lateral dinâmico, montado a partir da tabela de módulos.

Cada Modulo com `rota` vira um item (nome, ícone, rota e ordem vêm do
banco). Só aparecem módulos que o usuário pode usar: liberados pelo
segmento/cliente (web_project.acesso.modulos_habilitados) E com a permissão
do perfil. A mesma regra protege as URLs (module_required) — esconder o
item aqui é só conveniência.
"""
from django.urls import NoReverseMatch, reverse

from apps.modulo.models import Modulo

from .acesso import modulos_habilitados

ICONE_PADRAO = "ti tabler-app-window"


def _item(modulo):
    return {
        "url": modulo.rota,
        "name": modulo.nome,
        "slug": modulo.slug,
        "icon": f"menu-icon icon-base {modulo.icone or ICONE_PADRAO}",
    }


def montar_menu(user):
    """Lista de itens no formato esperado por menu_item_template.html."""
    if not user.is_authenticated:
        return []
    modulos = Modulo.objects.filter(slug__in=modulos_habilitados(user)).exclude(rota="")  # já ordenados (ordem, nome)
    principal, configuracoes = [], []
    for modulo in modulos:
        if modulo.permissao and not user.has_perm(modulo.permissao):
            continue
        try:
            reverse(modulo.rota)
        except NoReverseMatch:  # rota cadastrada errada não pode derrubar todas as telas
            continue
        destino = configuracoes if modulo.secao == Modulo.SECAO_CONFIGURACOES else principal
        destino.append(_item(modulo))
    if configuracoes:
        principal.append({
            "name": "Configurações", "slug": "configuracoes",
            "icon": "menu-icon icon-base ti tabler-settings", "submenu": configuracoes,
        })
    return principal
