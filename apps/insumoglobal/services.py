"""Regras do cadastro global: achar o padrão, sugerir semelhantes, avisar o administrador e padronizar (promover ou redirecionar)."""
import difflib
import logging

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.db.models import Q
from django.urls import reverse

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao, registrar_exclusao
from apps.notificacao.services import enviar_para_usuarios
from web_project.historico import HistoricoImutavelError
from web_project.regras import RegraNegocioError

from .models import InsumoGlobal, InsumoGlobalMarca, MarcaGlobal, normalizar

logger = logging.getLogger(__name__)
LIMIAR_SEMELHANTE = 0.8  # similaridade mínima para considerar "provável erro de digitação" ao cadastrar
PERMISSAO_AVISO = "insumoglobal.view_naoconformes"


def achar_exato(nome):
    """Padrão ATIVO com o mesmo nome (sem acento/caixa)."""
    return InsumoGlobal.objects.filter(nome_normalizado=normalizar(nome), ativo=True).first()


def semelhantes(nome, limite=3, corte=LIMIAR_SEMELHANTE):
    """Até `limite` padrões ativos parecidos com `nome` (erro de digitação, plural, acento)."""
    alvo = normalizar(nome)
    por_chave = {g.nome_normalizado: g for g in InsumoGlobal.objects.filter(ativo=True)}
    return [por_chave[c] for c in difflib.get_close_matches(alvo, list(por_chave), n=limite, cutoff=corte)]


def administradores_do_cadastro():
    """Quem recebe o aviso: superusuários e quem tem a permissão de ver não conformidades (por papel ou individual)."""
    candidatos = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__permissions__codename="view_naoconformes") | Q(permissoes_individuais__permissao__codename="view_naoconformes")
    ).distinct()
    return [u for u in candidatos if u.is_superuser or u.has_perm(PERMISSAO_AVISO)]


def avisar_novo_fora_do_padrao(insumo):
    """Notifica os administradores. Nunca derruba o cadastro: falha só vai para o log."""
    try:
        destinatarios = administradores_do_cadastro()
        if destinatarios:
            enviar_para_usuarios(
                "Insumo fora do padrão",
                f"“{insumo.nome}” foi cadastrado por {insumo.cliente.nome_razao_social} e não existe no cadastro global. Padronize ou redirecione.",
                reverse("insumo-nao-conformes"), destinatarios,
            )
    except Exception:
        logger.exception("Falha ao notificar insumo fora do padrão")


# --------------------------------------------------------------------------
# Padronização (ações do administrador)
# --------------------------------------------------------------------------

def promover_a_global(request, insumo):
    """O insumo fora do padrão vira um NOVO padrão global (nome e unidade dele) e passa a conforme."""
    with transaction.atomic():
        if achar_exato(insumo.nome) is not None:
            raise RegraNegocioError("Já existe um padrão com este nome: use Redirecionar.")
        novo = InsumoGlobal(nome=insumo.nome, medida=insumo.medida, criado_por=getattr(request, "user", None))
        novo.save()
        registrar_criacao(request, novo, modulo="insumoglobal")
        _vincular(request, insumo, novo, novo.nome)
    return novo


def redirecionar(request, insumo, destino):
    """Aponta o insumo fora do padrão para `destino` (padrão global). Se a unidade do cliente JÁ tem o insumo padronizado, as referências
    (fichas, estoque, cotações...) são movidas para ele e o duplicado é excluído; senão ele só é renomeado para o padrão.
    Devolve o insumo que ficou valendo."""
    if not destino.ativo:
        raise RegraNegocioError("O padrão de destino está inativo.")
    modelo = type(insumo)
    with transaction.atomic():
        existente = modelo.objects.filter(cliente=insumo.cliente).exclude(pk=insumo.pk).filter(Q(insumo_global=destino) | Q(nome__iexact=destino.nome)).first()
        if existente is None:
            _vincular(request, insumo, destino, destino.nome)
            return insumo
        _mesclar(request, origem=insumo, destino=existente, padrao=destino)
        return existente


def _vincular(request, insumo, global_, nome):
    antes_nome, antes_padrao = insumo.nome, "fora do padrão" if insumo.nao_conforme else "sem vínculo"
    insumo.nome, insumo.insumo_global, insumo.nao_conforme = nome, global_, False
    insumo.insumo_global_marca = variacao_para(global_, insumo.marca)  # a marca digitada pode já ser uma variação do padrão
    if insumo.insumo_global_marca is not None:
        insumo.marca = insumo.insumo_global_marca.marca.nome
    insumo.save()
    registrar_alteracoes(
        request, insumo, [
            {"campo": "nome", "rotulo": "Insumo", "antes": antes_nome, "depois": nome},
            {"campo": "padrao", "rotulo": "Padronização", "antes": antes_padrao, "depois": f"padronizado como “{global_.nome}”"},
        ], modulo="insumo",
    )


def _mesclar(request, origem, destino, padrao):
    """Move TODAS as referências de `origem` para `destino` e exclui `origem` (ou o inativa se algo ainda o protege)."""
    for relacao in type(origem)._meta.related_objects:
        if relacao.many_to_many or not (relacao.one_to_many or relacao.one_to_one):
            continue
        modelo, campo = relacao.related_model, relacao.field.name
        try:
            with transaction.atomic():
                modelo._default_manager.filter(**{campo: origem}).update(**{campo: destino})
        except HistoricoImutavelError:
            continue  # histórico imutável (ex.: preços passados) permanece com o insumo original, que então é inativado em vez de excluído
        except IntegrityError:
            raise RegraNegocioError(f"Não foi possível mesclar: “{destino.nome}” já aparece junto com este insumo em {modelo._meta.verbose_name_plural}.") from None
    if destino.insumo_global_id != padrao.pk or destino.nao_conforme:
        destino.insumo_global, destino.nao_conforme = padrao, False
        destino.save(update_fields=["insumo_global", "nao_conforme", "atualizado_em"])
    nome_origem = origem.nome
    try:
        with transaction.atomic():
            registrar_exclusao(request, origem, modulo="insumo")
            origem.delete()
    except (ProtectedError, HistoricoImutavelError):
        origem.ativo, origem.nao_conforme = False, False
        origem.save(update_fields=["ativo", "nao_conforme", "atualizado_em"])
        registrar_alteracoes(request, origem, [{"campo": "ativo", "rotulo": "Situação", "antes": "ativo (fora do padrão)", "depois": f"inativado: mesclado em “{destino.nome}”"}], modulo="insumo")
    logger.info("Insumo fora do padrão %r mesclado em %r", nome_origem, destino.nome)


# --------------------------------------------------------------------------
# Marcas: variação (insumo padrão + marca), cada uma com a sua foto
# --------------------------------------------------------------------------

def adicionar_marca(request, insumo_global, nome_marca):
    """Garante a MarcaGlobal (única por nome sem acento/caixa) e a variação do insumo com ela. Devolve (variação, criada)."""
    nome = " ".join((nome_marca or "").split())
    if not nome:
        raise RegraNegocioError("Informe a marca.")
    if len(nome) > 60:
        raise RegraNegocioError("A marca pode ter até 60 caracteres.")
    with transaction.atomic():
        marca = MarcaGlobal.objects.filter(nome_normalizado=normalizar(nome)).first()
        if marca is None:
            marca = MarcaGlobal(nome=nome)
            marca.save()
            registrar_criacao(request, marca, modulo="insumoglobal")
        variacao, criada = InsumoGlobalMarca.objects.get_or_create(insumo_global=insumo_global, marca=marca)
        if criada:
            registrar_criacao(request, variacao, modulo="insumoglobal")
        elif not variacao.ativo:
            variacao.ativo, criada = True, True
            variacao.save(update_fields=["ativo"])
    return variacao, criada


def variacao_para(insumo_global, marca):
    """Variação ATIVA do insumo padrão para o texto de marca digitado (ou None)."""
    if insumo_global is None or not (marca or "").strip():
        return None
    return InsumoGlobalMarca.objects.filter(
        insumo_global=insumo_global, ativo=True, marca__ativo=True, marca__nome_normalizado=normalizar(marca),
    ).select_related("marca").first()
