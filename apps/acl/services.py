"""Criar/editar/excluir papéis (Group) com auditoria — usado pela tela web e pela API."""
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao, registrar_exclusao

from web_project.acesso import GRUPO_ADMINISTRADOR

from .models import PermissaoUsuario
from .permissions_matrix import ids_validos_da_matriz, montar_matriz_permissoes


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
    if grupo is not None and grupo.name == GRUPO_ADMINISTRADOR and nome != GRUPO_ADMINISTRADOR:
        raise ValidationError("O perfil Admin não pode ser renomeado.")
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
    if grupo.name == GRUPO_ADMINISTRADOR:
        raise ValidationError("O perfil Admin não pode ser excluído.")
    with transaction.atomic():
        registrar_exclusao(request, grupo, modulo="acl")  # antes do delete(): depois o objeto perde o id
        grupo.delete()


# --------------------------------------------------------------------------
# Permissões individuais (exceções por usuário em relação ao papel)
# --------------------------------------------------------------------------

PREFIXO_CAMPO = "perm_"  # campos do formulário de usuário: perm_<id da Permission> = "" | permitir | negar
EFEITOS_VALIDOS = {"", PermissaoUsuario.EFEITO_PERMITIR, PermissaoUsuario.EFEITO_NEGAR}


def efeitos_do_usuario(usuario):
    """{id da Permission: 'permitir'|'negar'} das exceções do usuário."""
    if usuario is None or usuario.pk is None:
        return {}
    return dict(usuario.permissoes_individuais.values_list("permissao_id", "efeito"))


def matriz_do_usuario(efeitos):
    """A mesma matriz módulo x ação da edição de papéis, com o `efeito`
    ('', 'permitir' ou 'negar') de cada célula."""
    matriz = montar_matriz_permissoes()
    for linha in matriz:
        for acao in linha["acoes"]:
            acao["efeito"] = efeitos.get(acao["permissao_id"], "")
    return matriz


def ler_efeitos(dados):
    """Extrai {id: efeito} dos campos perm_<id> enviados. Levanta ValidationError
    para ids fora da matriz ou valores desconhecidos (POST manipulado).
    Devolve None se o formulário não trouxe nenhum campo (ex.: API): nada muda."""
    campos = {k[len(PREFIXO_CAMPO):]: dados[k] for k in dados if k.startswith(PREFIXO_CAMPO)}
    if not campos:
        return None
    validos = ids_validos_da_matriz()
    efeitos = {}
    for chave, efeito in campos.items():
        if not chave.isdigit() or int(chave) not in validos or efeito not in EFEITOS_VALIDOS:
            raise ValidationError("Permissões individuais inválidas.")
        efeitos[int(chave)] = efeito
    return efeitos


def validar_concessoes(solicitante, usuario, efeitos):
    """Ninguém concede a outro (nem a si) o que não tem: novas exceções
    'permitir' só valem para permissões que o solicitante possui. Exceções
    já existentes e inalteradas continuam aceitas. Levanta ValidationError."""
    atuais = efeitos_do_usuario(usuario)
    novas = [
        pk for pk, efeito in efeitos.items()
        if efeito == PermissaoUsuario.EFEITO_PERMITIR and atuais.get(pk) != PermissaoUsuario.EFEITO_PERMITIR
    ]
    for permissao in Permission.objects.filter(pk__in=novas).select_related("content_type"):
        if not solicitante.has_perm(f"{permissao.content_type.app_label}.{permissao.codename}"):
            raise ValidationError("Você não pode permitir uma ação que você mesmo não possui.")


def texto_das_excecoes(efeitos):
    """Texto legível para a auditoria, ex.: 'Permitir: insumo.add_insumo; Negar: ...'."""
    if not efeitos:
        return None
    nomes = {
        p.pk: f"{p.content_type.app_label}.{p.codename}"
        for p in Permission.objects.filter(pk__in=efeitos).select_related("content_type")
    }
    partes = []
    for efeito, rotulo in PermissaoUsuario.EFEITO_CHOICES:
        itens = sorted(nomes[pk] for pk, e in efeitos.items() if e == efeito and pk in nomes)
        if itens:
            partes.append(f"{rotulo}: {', '.join(itens)}")
    return "; ".join(partes) or None


def salvar_excecoes(usuario, efeitos):
    """Grava as exceções de UM usuário. Só mexe nas permissões informadas
    em `efeitos`; '' (herdar) remove a exceção. Chamar dentro de transação."""
    for pk, efeito in efeitos.items():
        if efeito:
            PermissaoUsuario.objects.update_or_create(usuario=usuario, permissao_id=pk, defaults={"efeito": efeito})
        else:
            PermissaoUsuario.objects.filter(usuario=usuario, permissao_id=pk).delete()
