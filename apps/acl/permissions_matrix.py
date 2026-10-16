"""
Monta a matriz "módulo x ação" (Visualizar/Criar/Editar/Excluir) usada
pelas telas de Papéis e Permissões, reaproveitando as Permission nativas
que o Django já gera automaticamente para cada model (ver Group-ACL.md).

As linhas vêm da tabela de módulos: todo Modulo `delegavel` e `ativo` com
uma `permissao` ("app.view_modelo") aparece aqui automaticamente — cadastrar
o módulo é suficiente, não há lista paralela para manter.

Módulos NÃO delegáveis (ex.: gerenciar papéis/permissões, auditoria) ficam
de fora de propósito: é uma capacidade sensível, concedida só ao grupo
Administrador (ver auth/management/commands/seed_acl.py).
"""
from django.contrib.auth.models import Permission

from apps.modulo.models import Modulo

ACOES = ["view", "add", "change", "delete"]
ACOES_LABEL = {
    "view": "Visualizar",
    "add": "Criar",
    "change": "Editar",
    "delete": "Excluir",
}


def _app_e_modelo(permissao):
    """'insumo.view_insumo' -> ('insumo', 'insumo')."""
    app, codename = permissao.split(".", 1)
    return app, codename.split("_", 1)[1]


def _modulos_da_matriz():
    return [m for m in Modulo.objects.filter(ativo=True, delegavel=True).exclude(permissao="") if "." in m.permissao]


def _permissoes_por_chave(modulos):
    """{(app, modelo, ação): Permission} para todos os módulos da matriz,
    em uma única consulta."""
    alvos = {_app_e_modelo(m.permissao) for m in modulos}
    permissoes = Permission.objects.filter(content_type__app_label__in={a for a, _ in alvos}).select_related("content_type")
    encontradas = {}
    for perm in permissoes:
        acao, _, modelo = perm.codename.partition("_")
        if acao in ACOES and (perm.content_type.app_label, modelo) in alvos:
            encontradas[(perm.content_type.app_label, modelo, acao)] = perm
    return encontradas


def montar_matriz_permissoes(permissoes_marcadas_ids=None):
    """Retorna uma lista de linhas (uma por módulo), cada uma com as 4
    ações e o id da Permission correspondente (ou None se o model não
    tiver aquela ação).

    `permissoes_marcadas_ids`: ids de Permission já atribuídas ao papel
    sendo editado, para marcar os checkboxes correspondentes.
    """
    marcadas = set(permissoes_marcadas_ids or [])
    modulos = _modulos_da_matriz()
    permissoes = _permissoes_por_chave(modulos)

    matriz = []
    for modulo in modulos:
        app, modelo = _app_e_modelo(modulo.permissao)
        linha = {"app_label": app, "nome": modulo.nome, "acoes": []}
        for acao in ACOES:
            perm = permissoes.get((app, modelo, acao))
            linha["acoes"].append(
                {
                    "acao": acao,
                    "label": ACOES_LABEL[acao],
                    "permissao_id": perm.pk if perm else None,
                    "marcada": bool(perm and perm.pk in marcadas),
                }
            )
        matriz.append(linha)
    return matriz


def _permissoes_adicionais():
    """Permissões dos apps de negócio/administração que não estão na matriz
    (ex.: abrir caixa, ver caixas, sangria, mover pedido, outros modelos).
    Apps do próprio Django (auth, admin, sessions...) ficam de fora de propósito."""
    from django.apps import apps

    from .padroes import ADMIN_APPS, SCREEN_APPS

    na_matriz = {p.pk for p in _permissoes_por_chave(_modulos_da_matriz()).values()}
    permissoes = Permission.objects.filter(content_type__app_label__in=[*SCREEN_APPS, *ADMIN_APPS]).select_related("content_type")
    resultado = []
    for perm in permissoes:
        if perm.pk in na_matriz:
            continue
        acao, _, modelo = perm.codename.partition("_")
        modelo_cls = perm.content_type.model_class()
        if acao in ACOES and modelo_cls is not None:
            rotulo = f"{ACOES_LABEL[acao]} — {modelo_cls._meta.verbose_name}"
        else:
            rotulo = perm.name  # permissão personalizada: usa a descrição do model
        app_config = apps.get_app_config(perm.content_type.app_label)
        resultado.append((app_config.verbose_name, perm, rotulo))
    return resultado


def montar_permissoes_adicionais(permissoes_marcadas_ids=None):
    """Permissões que não cabem na matriz módulo x ação, agrupadas por app:
    [{"grupo": "Pdv", "permissoes": [{"permissao_id", "label", "codigo", "marcada"}]}]."""
    marcadas = set(permissoes_marcadas_ids or [])
    grupos = {}
    for grupo, perm, rotulo in _permissoes_adicionais():
        grupos.setdefault(grupo, []).append(
            {"permissao_id": perm.pk, "label": rotulo, "codigo": f"{perm.content_type.app_label}.{perm.codename}", "marcada": perm.pk in marcadas}
        )
    return [
        {"grupo": grupo, "permissoes": sorted(itens, key=lambda i: i["label"])}
        for grupo, itens in sorted(grupos.items())
    ]


def ids_validos_da_matriz():
    """Todos os ids de Permission que a matriz conhece (módulos delegáveis +
    permissões adicionais dos apps do sistema) — usado para nunca aceitar,
    ao salvar um papel, um id fora disso (ex: alguém tentando, via POST
    manipulado, conceder "auth.delete_group" marcando um id arbitrário)."""
    ids = {perm.pk for perm in _permissoes_por_chave(_modulos_da_matriz()).values()}
    ids.update(perm.pk for _, perm, _ in _permissoes_adicionais())
    return ids
