"""Menu lateral dinâmico, montado a partir da tabela de módulos.

Cada Modulo com `rota` vira um item (nome, ícone, rota e ordem vêm do
banco). Só aparecem módulos que o usuário pode usar: liberados pelo
segmento/cliente (web_project.acesso.modulos_habilitados) E com a permissão
do perfil. A mesma regra protege as URLs (module_required) — esconder o
item aqui é só conveniência.
"""
from django.urls import NoReverseMatch, reverse

from apps.modulo.models import Modulo, OrdemMenuCliente

from .acesso import cliente_do_usuario, modulos_habilitados

ICONE_PADRAO = "ti tabler-app-window"
# Telas do PDV: módulos-filhos do módulo "pdv", mostrados juntos num submenu "PDV" (na ordem abaixo).
TELAS_PDV = ("pdv-vender", "pdv-comandas", "pdv-caixa", "pdv-vendas", "pdv-caixas")


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
    modulos = list(Modulo.objects.filter(slug__in=modulos_habilitados(user)).exclude(rota=""))  # já ordenados (ordem, nome)
    # Ordem personalizada do cliente do usuário (Configurações > Módulos do cliente); módulo sem linha mantém a ordem padrão.
    cliente = cliente_do_usuario(user)
    if cliente is not None:
        personalizada = dict(OrdemMenuCliente.objects.filter(cliente=cliente).values_list("modulo_id", "ordem"))
        if personalizada:
            modulos.sort(key=lambda m: (personalizada.get(m.pk, m.ordem), m.nome))
    principal, configuracoes, grupo_pdv = [], [], None
    for modulo in modulos:
        if modulo.permissao and not user.has_perm(modulo.permissao):
            continue
        try:
            reverse(modulo.rota)
        except NoReverseMatch:  # rota cadastrada errada não pode derrubar todas as telas
            continue
        if modulo.slug in TELAS_PDV:
            item = _item(modulo)
            item["name"] = modulo.nome.split("— ", 1)[-1]  # "PDV — Vender" -> "Vender" (o grupo já se chama PDV)
            if grupo_pdv is None:
                grupo_pdv = {"name": "PDV", "slug": "pdv", "icon": "menu-icon icon-base ti tabler-cash-register", "submenu": []}
                principal.append(grupo_pdv)  # entra na posição da primeira tela do PDV
            grupo_pdv["submenu"].append(item)
            continue
        destino = configuracoes if modulo.secao == Modulo.SECAO_CONFIGURACOES else principal
        destino.append(_item(modulo))
    if grupo_pdv:
        grupo_pdv["submenu"].sort(key=lambda i: TELAS_PDV.index(i["slug"]))
    if configuracoes:
        principal.append({
            "name": "Configurações", "slug": "configuracoes",
            "icon": "menu-icon icon-base ti tabler-settings", "submenu": configuracoes,
        })
    return principal
