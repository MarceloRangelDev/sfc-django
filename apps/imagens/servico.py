"""Serviço ÚNICO de imagens: upload, leitura/entrega, substituição, exclusão, limpeza e contadores de uso.

Os módulos (insumo, produto, usuário, cliente...) só chamam estas funções informando o tipo da entidade e o ID; nunca falam com o R2.
Toda operação: (1) valida o conteúdo, (2) autoriza usuário+tenant em `registro.py`, (3) grava metadados, (4) audita.

Falha parcial (R2 x banco) — ordem escolhida para nunca perder o original nem deixar lixo invisível:
  1. grava a linha `pendente`; 2. envia ao R2; 3. numa transação: ativa a nova, desativa a anterior e audita.
  Se (2) falhar a linha é descartada. Se (3) falhar, o objeto é removido (compensação); se nem isso for possível, a linha `pendente`
  continua e `limpar_imagens` remove o objeto órfão depois. O original substituído só é apagado do R2 após a retenção.
"""
import logging
import uuid as uuidlib
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import ACAO_CRIAR, ACAO_EDITAR, ACAO_EXCLUIR
from apps.auditoria.utils import registrar

from . import armazenamento, processamento, registro
from .models import Imagem, TransformacaoUnica, UsoImagens

logger = logging.getLogger(__name__)
MODULO_AUDITORIA = "imagens"
EXTENSOES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


# --------------------------------------------------------------------------
# Contadores de uso (base do painel de custos)
# --------------------------------------------------------------------------

def contar(cliente_id, metrica, quantidade=1):
    """Soma `quantidade` ao contador do dia. Nunca derruba a operação principal."""
    try:
        contador, _ = UsoImagens.objects.get_or_create(dia=timezone.localdate(), cliente_id=cliente_id, metrica=metrica)
        UsoImagens.objects.filter(pk=contador.pk).update(quantidade=F("quantidade") + quantidade)
    except Exception:
        logger.exception("Falha ao contar uso de imagens")


def permitir(user, escopo, maximo):
    """Limite de requisições por usuário por minuto (cache). True se ainda dentro do limite."""
    chave = f"imagens:{escopo}:{user.pk}:{timezone.now():%Y%m%d%H%M}"
    cache.add(chave, 0, 90)
    return cache.incr(chave) <= maximo


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

def ativa(tipo, entidade_id):
    return Imagem.objects.filter(entidade_tipo=tipo, entidade_id=entidade_id, estado=Imagem.ATIVA).first()


def url(imagem, variante="lista"):
    """URL do APLICATIVO (estável, autorizada a cada acesso). O uuid muda a cada troca, o que invalida o cache dos navegadores."""
    return reverse("imagens-entrega", args=[imagem.uuid, variante])


def url_da_entidade(tipo, entidade_id, variante="lista"):
    imagem = ativa(tipo, entidade_id)
    return url(imagem, variante) if imagem else ""


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------

def _auditar(request, usuario, acao, imagem, descricao):
    registrar(
        request, acao, modulo=MODULO_AUDITORIA, registro_id=imagem.pk, usuario=usuario, tabela="imagens_imagem",
        registro_nome=f"{imagem.entidade_tipo} {imagem.entidade_id}", descricao=descricao, silencioso=False,
    )


def enviar(user, tipo, entidade_id, dados, request=None):
    """Cria a imagem da entidade ou SUBSTITUI a atual. Reenviar o mesmo arquivo é idempotente (devolve a imagem já ativa)."""
    reg = registro.entidade(tipo)
    objeto = reg.obter_para_alterar(user, entidade_id) if reg else None
    if objeto is None:
        raise Http404  # não confirma a existência de entidades de outro tenant
    mime, largura, altura, checksum = processamento.validar(dados)
    cliente = reg.cliente_de(objeto)
    if cliente is None:
        raise processamento.ImagemInvalida("Este registro não está vinculado a uma unidade.")
    atual = ativa(tipo, objeto.pk)
    if atual and atual.checksum == checksum:
        return atual
    deposito = armazenamento.obter()

    identificador = uuidlib.uuid4()
    chave = f"t{cliente.pk}/{tipo}/{identificador}.{EXTENSOES[mime]}"  # tenant + tipo + UUID; o caminho NÃO substitui a autorização
    imagem = Imagem.objects.create(
        uuid=identificador, cliente=cliente, entidade_tipo=tipo, entidade_id=objeto.pk, chave=chave, mime=mime, tamanho=len(dados),
        largura=largura, altura=altura, checksum=checksum, criado_por=user,
    )
    try:
        deposito.enviar(chave, dados, mime)
        contar(cliente.pk, "classe_a")
    except armazenamento.ArmazenamentoIndisponivel:
        imagem.delete()
        raise
    try:
        with transaction.atomic():
            anterior = Imagem.objects.select_for_update().filter(entidade_tipo=tipo, entidade_id=objeto.pk, estado=Imagem.ATIVA).first()
            if anterior:
                anterior.estado, anterior.removida_em = Imagem.PARA_EXCLUIR, timezone.now()
                anterior.save(update_fields=["estado", "removida_em", "atualizado_em"])
            imagem.estado = Imagem.ATIVA
            imagem.save(update_fields=["estado", "atualizado_em"])
            _auditar(request, user, ACAO_EDITAR if anterior else ACAO_CRIAR, imagem, f"{'Substituída' if anterior else 'Enviada'} imagem de {reg.rotulo} ID {objeto.pk}.")
    except Exception:
        _compensar(imagem, deposito)
        raise
    return imagem


def _compensar(imagem, deposito):
    """Desfaz o objeto do R2 quando o banco falhou. Se também falhar, a linha fica `pendente` e a limpeza resolve."""
    try:
        deposito.excluir(imagem.chave)
        Imagem.objects.filter(pk=imagem.pk).update(estado=Imagem.EXCLUIDA)
    except Exception:
        logger.warning("Compensação do upload %s pendente de limpeza", imagem.uuid)


def remover(user, tipo, entidade_id, request=None):
    """Exclusão explícita pelo usuário: o objeto sai do R2 na hora (se o R2 falhar, fica `para_excluir` e a limpeza conclui)."""
    reg = registro.entidade(tipo)
    objeto = reg.obter_para_alterar(user, entidade_id) if reg else None
    imagem = ativa(tipo, entidade_id) if objeto is not None else None
    if imagem is None:
        raise Http404
    with transaction.atomic():
        imagem.estado, imagem.removida_em = Imagem.PARA_EXCLUIR, timezone.now()
        imagem.save(update_fields=["estado", "removida_em", "atualizado_em"])
        _auditar(request, user, ACAO_EXCLUIR, imagem, f"Removida imagem de {reg.rotulo} ID {entidade_id}.")
    if not _apagar_objeto(imagem):  # R2 fora do ar: vence a retenção já, para a próxima limpeza concluir a exclusão
        Imagem.objects.filter(pk=imagem.pk).update(removida_em=timezone.now() - timedelta(days=settings.IMAGENS_RETENCAO_DIAS))


def desvincular(tipo, entidade_id):
    """Chamado quando a ENTIDADE é excluída: a imagem não é apagada na hora, apenas deixa de ser servida e segue a retenção."""
    Imagem.objects.filter(entidade_tipo=tipo, entidade_id=entidade_id, estado=Imagem.ATIVA).update(estado=Imagem.PARA_EXCLUIR, removida_em=timezone.now())


def _apagar_objeto(imagem):
    try:
        armazenamento.obter().excluir(imagem.chave)  # DeleteObject é gratuito no R2 (não conta como Classe A)
    except armazenamento.ArmazenamentoIndisponivel:
        return False
    Imagem.objects.filter(pk=imagem.pk).update(estado=Imagem.EXCLUIDA)
    return True


def limpar(agora=None):
    """Remove do R2: (a) substituídas/removidas após a retenção, (b) envios nunca confirmados com mais de 1 hora. Devolve (apagadas, falhas)."""
    agora = agora or timezone.now()
    limite_retencao = agora - timedelta(days=settings.IMAGENS_RETENCAO_DIAS)
    candidatas = list(Imagem.objects.filter(estado=Imagem.PARA_EXCLUIR, removida_em__lte=limite_retencao))
    candidatas += list(Imagem.objects.filter(estado=Imagem.PENDENTE, criado_em__lte=agora - timedelta(hours=1)))
    apagadas = sum(1 for imagem in candidatas if _apagar_objeto(imagem))
    return apagadas, len(candidatas) - apagadas


# --------------------------------------------------------------------------
# Entrega
# --------------------------------------------------------------------------

def variante_bytes(imagem, variante, cabecalho_accept):
    """Modo `local`: gera (ou pega do cache) a variante no formato negociado. Devolve (bytes, mime)."""
    formato, mime = processamento.formato_saida(cabecalho_accept)
    chave_cache = f"imagens:var:{imagem.uuid}:{variante}:{formato}"
    pronto = cache.get(chave_cache)
    if pronto is None:
        original = armazenamento.obter().ler(imagem.chave)
        contar(imagem.cliente_id, "classe_b")
        pronto = processamento.gerar_variante(original, variante, formato)
        cache.set(chave_cache, pronto, 3600)
    return pronto, mime


def entrega_cloudflare(imagem):
    """True quando esta imagem pode sair pelas Image Transformations: modo ligado e categoria CATÁLOGO (privadas nunca)."""
    reg = registro.entidade(imagem.entidade_tipo)
    return bool(settings.IMAGENS_ENTREGA == "cloudflare" and settings.IMAGENS_CF_BASE_URL and reg and reg.categoria == registro.CATALOGO)


def url_origem(imagem, base):
    """URL estável (assinada, sem expiração) por onde o Cloudflare busca o ORIGINAL de uma imagem de catálogo. Só serve catálogo."""
    return base + reverse("imagens-origem", args=[signing.Signer(salt="imagens-origem").sign(str(imagem.uuid))])


def url_cloudflare(imagem, variante, base_origem):
    lado, qualidade, quadrado = processamento.VARIANTES[variante]
    parametros = f"width={lado},height={lado},fit={'cover' if quadrado else 'scale-down'},quality={qualidade},format=auto,metadata=none"
    mes = timezone.localdate().strftime("%Y-%m")
    _, criada = TransformacaoUnica.objects.get_or_create(imagem=imagem, variante=variante, mes=mes, defaults={"cliente_id": imagem.cliente_id})
    if criada:
        contar(imagem.cliente_id, "transformacao")  # 1ª entrega do mês desta combinação = 1 transformação única (estimativa)
    return f"{settings.IMAGENS_CF_BASE_URL}/cdn-cgi/image/{parametros}/{url_origem(imagem, base_origem)}"


def original_por_token(token):
    """Valida o token assinado do endpoint de origem e devolve (imagem, bytes) — apenas imagens ATIVAS de CATÁLOGO."""
    try:
        identificador = signing.Signer(salt="imagens-origem").unsign(token)
    except signing.BadSignature:
        raise Http404 from None
    imagem = Imagem.objects.filter(uuid=identificador, estado=Imagem.ATIVA).first()
    reg = registro.entidade(imagem.entidade_tipo) if imagem else None
    if reg is None or reg.categoria != registro.CATALOGO:
        raise Http404
    contar(imagem.cliente_id, "classe_b")
    return imagem, armazenamento.obter().ler(imagem.chave)
