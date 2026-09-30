"""
Monta a matriz "módulo x ação" (Visualizar/Criar/Editar/Excluir) usada
pelas telas de Papéis e Permissões, reaproveitando as Permission nativas
que o Django já gera automaticamente para cada model (ver Group-ACL.md).

Deliberadamente NÃO inclui os módulos "auth" (Group/Permission do próprio
Django) nem "accounts" (Profile) nesta matriz: gerenciar quem pode
gerenciar papéis/permissões é uma capacidade sensível, concedida só ao
grupo Administrador (ver auth/management/commands/seed_acl.py), não algo
que se deva poder atribuir livremente a um papel qualquer por esta tela.
"""
from django.contrib.auth.models import Permission

# app_label -> nome de exibição do módulo.
MODULOS_GERENCIAVEIS = {
    "insumo": "Insumos",
    "categoria": "Categorias",
    "produto": "Produto",
    "processado": "Processados",
    "ficha": "Ficha Técnica",
    "precificacao": "Precificação",
    "cliente": "Clientes",
    "modulo": "Módulos",
}

ACOES = ["view", "add", "change", "delete"]
ACOES_LABEL = {
    "view": "Visualizar",
    "add": "Criar",
    "change": "Editar",
    "delete": "Excluir",
}


def montar_matriz_permissoes(permissoes_marcadas_ids=None):
    """Retorna uma lista de linhas (uma por módulo), cada uma com as 4
    ações e o id da Permission correspondente (ou None se o model não
    tiver aquela ação, o que não deve acontecer para os models atuais).

    `permissoes_marcadas_ids`: ids de Permission já atribuídas ao papel
    sendo editado, para marcar os checkboxes correspondentes.
    """
    marcadas = set(permissoes_marcadas_ids or [])

    permissoes = Permission.objects.filter(
        content_type__app_label__in=MODULOS_GERENCIAVEIS.keys()
    ).select_related("content_type")

    permissao_por_app_acao = {}
    for perm in permissoes:
        for acao in ACOES:
            if perm.codename.startswith(f"{acao}_"):
                permissao_por_app_acao[(perm.content_type.app_label, acao)] = perm
                break

    matriz = []
    for app_label, nome_modulo in MODULOS_GERENCIAVEIS.items():
        linha = {"app_label": app_label, "nome": nome_modulo, "acoes": []}
        for acao in ACOES:
            perm = permissao_por_app_acao.get((app_label, acao))
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
    nenhum dos módulos gerenciáveis (ex: alguém tentando, via POST
    manipulado, conceder "auth.delete_group" marcando um id arbitrário)."""
    permissoes = Permission.objects.filter(content_type__app_label__in=MODULOS_GERENCIAVEIS.keys())
    return set(permissoes.values_list("pk", flat=True))
