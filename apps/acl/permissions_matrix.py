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


def ids_validos_da_matriz():
    """Todos os ids de Permission que a matriz conhece — usado para nunca
    aceitar, ao salvar um papel, um id de permissão que não pertença a
    nenhum módulo delegável (ex: alguém tentando, via POST manipulado,
    conceder "auth.delete_group" marcando um id arbitrário)."""
    return {perm.pk for perm in _permissoes_por_chave(_modulos_da_matriz()).values()}
