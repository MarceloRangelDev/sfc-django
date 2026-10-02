"""Criar/editar/excluir papéis (Group) com auditoria — usado pela tela web e pela API."""
from django.contrib.auth.models import Group, Permission
from django.db import transaction

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao, registrar_exclusao

from .permissions_matrix import ids_validos_da_matriz


def alteracoes_de_permissoes(antes, depois):
    """Compara dois conjuntos de (codename, app_label) e devolve as
    alterações de auditoria: uma linha para as permissões concedidas e
    outra para as removidas."""

    def texto(conjunto):
        return ", ".join(sorted(f"{app}.{codigo}" for codigo, app in conjunto)) or None

    alteracoes = []
    if depois - antes:
        alteracoes.append({"campo": "permissoes_concedidas", "rotulo": "Permissões concedidas", "antes": None, "depois": texto(depois - antes)})
    if antes - depois:
        alteracoes.append({"campo": "permissoes_removidas", "rotulo": "Permissões removidas", "antes": texto(antes - depois), "depois": None})
    return alteracoes


def ids_de_permissao_aceitos(ids):
    """Só aceita ids que pertencem à matriz conhecida de módulos — mesmo que
    alguém envie outro id (ex.: de gerenciar papéis), ele é ignorado."""
    validos = ids_validos_da_matriz()
    return {int(i) for i in ids if str(i).isdigit() and int(i) in validos}


def salvar_papel(request, grupo, nome, ids_permissoes):
    """Cria (grupo=None) ou atualiza um papel e suas permissões. Devolve o Group.

    `ids_permissoes`: ids de Permission desejados (os inválidos são ignorados).
    """
    ids = ids_de_permissao_aceitos(ids_permissoes)
    criando = grupo is None
    with transaction.atomic():
        alteracoes = []
        if criando:
            grupo = Group.objects.create(name=nome)
            permissoes_antes = set()
        else:
            permissoes_antes = set(grupo.permissions.values_list("codename", "content_type__app_label"))
            if grupo.name != nome:
                alteracoes.append({"campo": "name", "rotulo": "Nome", "antes": grupo.name, "depois": nome})
                grupo.name = nome
                grupo.save()

        grupo.permissions.set(Permission.objects.filter(pk__in=ids))

        permissoes_depois = set(grupo.permissions.values_list("codename", "content_type__app_label"))
        alteracoes.extend(alteracoes_de_permissoes(permissoes_antes, permissoes_depois))
        if criando:
            registrar_criacao(request, grupo, modulo="acl")
        elif alteracoes:
            registrar_alteracoes(request, grupo, alteracoes, modulo="acl")
    return grupo


def excluir_papel(request, grupo):
    with transaction.atomic():
        registrar_exclusao(request, grupo, modulo="acl")  # antes do delete(): depois o objeto perde o id
        grupo.delete()
