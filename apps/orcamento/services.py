"""Regras do orçamento: cálculo, gravação (com auditoria), situação, "versão" para impressão/envio e envio por e-mail/WhatsApp.

Princípios:
- o servidor recalcula TUDO (linha, subtotal, descontos, total); o que o cliente (tela/API) manda como total é ignorado;
- o orçamento salvo só muda por uma gravação explícita. Imprimir/enviar usa uma VERSÃO (`Versao`) calculada em memória, sem os itens que o
  usuário escolheu omitir; remover os itens do orçamento salvo é outra ação, que exige confirmação (`aplicar_omissao_persistente`);
- falha de PDF, e-mail ou WhatsApp nunca perde nem altera o orçamento: o erro vira mensagem e um registro em OrcamentoEnvio;
- tudo é filtrado pela unidade (tenant) de quem chama; funções daqui recebem o orçamento já obtido do escopo do usuário.
"""
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.auditoria.utils import comparar, registrar, registrar_alteracoes, registrar_criacao, registrar_edicao, registrar_exclusao, tirar_foto
from apps.integracao import services as integracao
from apps.integracao.models import Conexao
from apps.precificacao.models import Precificacao
from apps.produto.models import Produto
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .models import Orcamento, OrcamentoEnvio, OrcamentoItem, OrcamentoLink

logger = logging.getLogger(__name__)
MAX_ITENS = 200
MAX_QUANTIDADE = Decimal("999999.999")
MAX_CENTAVOS = 10**12
VALIDADE_DO_LINK = timedelta(days=7)
PROVEDORES_WHATSAPP = ("whatsapp_cloud", "whatsapp_evolution", "whatsapp_intermediario")
MODULO = "orcamento"


# --------------------------------------------------------------------------
# Cálculo
# --------------------------------------------------------------------------

def _arredondar(valor):
    return int(Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def total_da_linha(quantidade, preco_unitario, desconto):
    """(valor bruto, total da linha) em centavos: quantidade × preço arredondado (meio para cima), menos o desconto da linha."""
    bruto = _arredondar(Decimal(quantidade) * Decimal(preco_unitario))
    if desconto > bruto:
        raise RegraNegocioError("O desconto de um item não pode ser maior que o valor do item.")
    return bruto, bruto - desconto


@dataclass
class Totais:
    subtotal: int
    desconto_itens: int
    desconto_geral: int
    total: int


def calcular_totais(itens, desconto_geral=0):
    """Totais de uma lista de itens (objetos com quantidade, preco_unitario e desconto) e do desconto geral."""
    subtotal = desconto_itens = 0
    for item in itens:
        bruto, _ = total_da_linha(item.quantidade, item.preco_unitario, item.desconto)
        subtotal += bruto
        desconto_itens += item.desconto
    if desconto_geral > subtotal - desconto_itens:
        raise RegraNegocioError("O desconto geral não pode ser maior que o total dos itens.")
    return Totais(subtotal, desconto_itens, desconto_geral, subtotal - desconto_itens - desconto_geral)


def preco_sugerido(cliente, produto):
    """Preço de venda da precificação marcada para o PDV do produto (a "oficial"); None se não houver."""
    precificacao = Precificacao.objects.filter(cliente=cliente, produto=produto, usar_no_pdv=True, ativo=True, preco_venda__gt=0).first()
    return precificacao.preco_venda if precificacao else None


# --------------------------------------------------------------------------
# Linhas (entrada) -> itens validados
# --------------------------------------------------------------------------

def validar_linhas(cliente, linhas, orcamento=None):
    """Valida as linhas recebidas (tela ou API) e devolve uma lista de OrcamentoItem SEM salvar. Cada linha: produto (id, opcional),
    tipo (só linha avulsa), descricao, quantidade, preco_unitario (centavos; omitido = preço do PDV do produto) e desconto (centavos).
    O produto precisa ser da MESMA unidade e ativo (ou já usado neste orçamento); o tipo vem do cadastro e a descrição padrão é o nome."""
    if not linhas:
        raise RegraNegocioError("Inclua ao menos um item no orçamento.")
    if len(linhas) > MAX_ITENS:
        raise RegraNegocioError(f"No máximo {MAX_ITENS} itens por orçamento.")
    ja_usados = set(orcamento.itens.exclude(produto=None).values_list("produto_id", flat=True)) if orcamento is not None and orcamento.pk else set()
    itens = []
    for ordem, linha in enumerate(linhas, 1):
        produto = None
        if linha.get("produto") not in (None, ""):
            try:
                produto = Produto.objects.get(pk=int(linha["produto"]), cliente=cliente)
            except (Produto.DoesNotExist, ValueError, TypeError):
                raise RegraNegocioError(f"Item {ordem}: produto/serviço inválido.") from None
            if not produto.ativo and produto.pk not in ja_usados:
                raise RegraNegocioError(f"Item {ordem}: {produto.nome} está inativo.")
        descricao = " ".join(str(linha.get("descricao") or (produto.nome if produto else "")).split())[:150]
        if not descricao:
            raise RegraNegocioError(f"Item {ordem}: informe a descrição.")
        tipo = produto.tipo if produto else str(linha.get("tipo") or OrcamentoItem.PRODUTO)
        if tipo not in dict(OrcamentoItem.TIPO_CHOICES):
            raise RegraNegocioError(f"Item {ordem}: tipo inválido.")
        try:
            quantidade = Decimal(str(linha.get("quantidade"))).quantize(Decimal("0.001"))
        except Exception:
            raise RegraNegocioError(f"Item {ordem}: quantidade inválida.") from None
        if not Decimal("0.001") <= quantidade <= MAX_QUANTIDADE:
            raise RegraNegocioError(f"Item {ordem}: a quantidade deve estar entre 0,001 e 999.999.")
        preco = linha.get("preco_unitario")
        if preco in (None, ""):
            preco = preco_sugerido(cliente, produto) if produto else None
            if preco is None:
                raise RegraNegocioError(f"Item {ordem}: informe o preço (o produto não tem precificação marcada para o PDV).")
        try:
            preco, desconto = int(preco), int(linha.get("desconto") or 0)
        except (TypeError, ValueError):
            raise RegraNegocioError(f"Item {ordem}: valores inválidos.") from None
        if not 0 <= preco <= MAX_CENTAVOS or not 0 <= desconto <= MAX_CENTAVOS:
            raise RegraNegocioError(f"Item {ordem}: valor fora do limite permitido.")
        item = OrcamentoItem(produto=produto, tipo=tipo, descricao=descricao, quantidade=quantidade, preco_unitario=preco, desconto=desconto, ordem=ordem)
        item.total = total_da_linha(quantidade, preco, desconto)[1]  # valida o desconto do item
        itens.append(item)
    return itens


# --------------------------------------------------------------------------
# Gravação
# --------------------------------------------------------------------------

def _proximo_numero(cliente):
    return (Orcamento.objects.filter(cliente=cliente).aggregate(m=Max("numero"))["m"] or 0) + 1


def _texto_itens(orcamento):
    return "; ".join(f"{i.descricao} x{i.quantidade.normalize():f}" for i in orcamento.itens.all()) or None


def invalidar_links(orcamento):
    """Alterou, cancelou ou excluiu: os links públicos antigos deixam de valer (o PDV mostraria conteúdo desatualizado)."""
    orcamento.links.all().delete()


def salvar_orcamento(request, orcamento, itens, foto_antes=None):
    """Grava o orçamento (já preenchido) e SUBSTITUI os itens por `itens` (OrcamentoItem de `validar_linhas`), recalculando os totais.
    Tudo ou nada. Só orçamento em rascunho/enviado é alterado. Devolve o orçamento."""
    criando = orcamento.pk is None
    if not criando and not orcamento.editavel:
        raise RegraNegocioError("Este orçamento está fechado (aprovado, recusado ou cancelado) e não pode ser alterado.")
    totais = calcular_totais(itens, orcamento.desconto)
    orcamento.subtotal, orcamento.desconto_itens, orcamento.total = totais.subtotal, totais.desconto_itens, totais.total
    for tentativa in range(3):  # a numeração é por unidade: duas gravações simultâneas podem disputar o mesmo número
        try:
            with transaction.atomic():
                antes = None if criando else _texto_itens(orcamento)
                if criando:
                    orcamento.numero = _proximo_numero(orcamento.cliente)
                orcamento.save()
                orcamento.itens.all().delete()
                for item in itens:
                    item.orcamento = orcamento
                OrcamentoItem.objects.bulk_create(itens)
                if criando:
                    registrar_criacao(request, orcamento, modulo=MODULO)
                else:
                    invalidar_links(orcamento)
                    alteracoes = comparar(foto_antes, tirar_foto(orcamento)) if foto_antes else []
                    depois = _texto_itens(orcamento)
                    if antes != depois:
                        alteracoes.append({"campo": "itens", "rotulo": "Itens do orçamento", "antes": antes, "depois": depois})
                    if alteracoes:
                        registrar_alteracoes(request, orcamento, alteracoes, modulo=MODULO)
            return orcamento
        except IntegrityError:
            if not criando or tentativa == 2:
                raise
    return orcamento


def mudar_situacao(request, orcamento, nova):
    """Aprova, recusa ou cancela um orçamento em aberto (rascunho/enviado). Aprovado/recusado/cancelado são finais."""
    if nova not in (Orcamento.APROVADO, Orcamento.RECUSADO, Orcamento.CANCELADO):
        raise RegraNegocioError("Situação inválida.")
    if not orcamento.editavel:
        raise RegraNegocioError("Este orçamento já está fechado.")
    foto = tirar_foto(orcamento)
    with transaction.atomic():
        orcamento.status = nova
        orcamento.save(update_fields=["status", "atualizado_em"])
        invalidar_links(orcamento)
        registrar_edicao(request, orcamento, foto, modulo=MODULO)
    return orcamento


def excluir_orcamento(request, orcamento):
    """Só rascunho (nunca enviado) é apagado; os demais devem ser cancelados para manter o histórico."""
    if orcamento.status != Orcamento.RASCUNHO or orcamento.envios.filter(canal__in=(OrcamentoEnvio.EMAIL, OrcamentoEnvio.WHATSAPP), status=OrcamentoEnvio.OK).exists():
        raise RegraNegocioError("Só orçamento em rascunho, nunca enviado, pode ser excluído. Use Cancelar para os demais.")
    with transaction.atomic():
        registrar_exclusao(request, orcamento, modulo=MODULO)
        orcamento.delete()


# --------------------------------------------------------------------------
# Versão para imprimir/enviar
# --------------------------------------------------------------------------

@dataclass
class Versao:
    orcamento: Orcamento
    itens: list
    omitidos: list
    totais: Totais


def versao_para_envio(orcamento, omitir_ids=()):
    """O que será impresso/enviado: os itens do orçamento SALVO menos os escolhidos para omitir (ids de outros orçamentos são ignorados).
    Nada é gravado. Os totais valem para esta versão; o desconto geral é mantido como está."""
    itens = list(orcamento.itens.all())
    pedidos = set()
    for i in omitir_ids or ():
        try:
            pedidos.add(int(i))
        except (TypeError, ValueError):
            continue
    omitidos = sorted(pedidos & {i.pk for i in itens})
    restantes = [i for i in itens if i.pk not in omitidos]
    if not restantes:
        raise RegraNegocioError("Mantenha ao menos um item na versão impressa/enviada.")
    return Versao(orcamento, restantes, omitidos, calcular_totais(restantes, orcamento.desconto))


def aplicar_omissao_persistente(request, orcamento, omitir_ids):
    """Remove DE VERDADE do orçamento salvo os itens escolhidos. Só roda quando o usuário confirma explicitamente (`persistir`); sem isso
    a omissão vale apenas para aquela versão. Recalcula os totais e audita."""
    if not orcamento.editavel:
        raise RegraNegocioError("Este orçamento está fechado e não pode ser alterado.")
    versao = versao_para_envio(orcamento, omitir_ids)  # valida: não deixa o orçamento sem itens nem com desconto geral maior que o total
    if not versao.omitidos:
        return orcamento
    foto = tirar_foto(orcamento)
    with transaction.atomic():
        antes = _texto_itens(orcamento)
        orcamento.itens.filter(pk__in=versao.omitidos).delete()
        orcamento.subtotal, orcamento.desconto_itens, orcamento.total = versao.totais.subtotal, versao.totais.desconto_itens, versao.totais.total
        orcamento.save(update_fields=["subtotal", "desconto_itens", "total", "atualizado_em"])
        invalidar_links(orcamento)
        alteracoes = comparar(foto, tirar_foto(orcamento))
        alteracoes.append({"campo": "itens", "rotulo": "Itens do orçamento", "antes": antes, "depois": _texto_itens(orcamento)})
        registrar_alteracoes(request, orcamento, alteracoes, modulo=MODULO)
    return orcamento


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------

def _mascarar(destino):
    destino = str(destino or "")
    if "@" in destino:
        usuario, dominio = destino.split("@", 1)
        return f"{usuario[:1]}***@{dominio}"[:80]
    return f"***{destino[-4:]}" if len(destino) > 4 else "***"


def registrar_envio(request, orcamento, canal, status, destino="", detalhe="", omitidos=()):
    return OrcamentoEnvio.objects.create(
        orcamento=orcamento, canal=canal, status=status, destino=_mascarar(destino) if destino else "", detalhe=str(detalhe)[:255], itens_omitidos=list(omitidos),
        criado_por=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
    )


def email_disponivel():
    """Há como enviar e-mail neste ambiente? Com o backend do Resend exige chave e remetente; outros backends valem como configurados."""
    if str(settings.EMAIL_BACKEND).endswith("ResendEmailBackend"):
        return bool(settings.RESEND_API_KEY and settings.MAIL_FROM_EMAIL)
    return True


def conexoes_whatsapp(orcamento):
    """Conexões de WhatsApp ATIVAS da unidade do orçamento (nunca de outra unidade)."""
    return Conexao.objects.filter(cliente=orcamento.cliente, status=Conexao.CONECTADA, provedor__in=PROVEDORES_WHATSAPP).order_by("nome")


def canais_disponiveis(user, orcamento):
    """{"email": bool, "whatsapp": [conexões]} para ESTE usuário: precisa da permissão de enviar orçamento e, no WhatsApp, também da de enviar
    mensagens das integrações; o canal precisa estar configurado e ativo."""
    pode = user.has_perm("orcamento.enviar_orcamento")
    return {
        "email": pode and email_disponivel(),
        "whatsapp": list(conexoes_whatsapp(orcamento)) if pode and user.has_perm("integracao.enviar_mensagem") else [],
    }


def _marcar_enviado(orcamento):
    campos = ["atualizado_em"]
    if orcamento.status == Orcamento.RASCUNHO:
        orcamento.status = Orcamento.ENVIADO
        campos.append("status")
    if orcamento.enviado_em is None:
        orcamento.enviado_em = timezone.now()
        campos.append("enviado_em")
    orcamento.save(update_fields=campos)


def _exigir_pode_enviar(request, orcamento):
    if not request.user.has_perm("orcamento.enviar_orcamento"):
        raise RegraNegocioError("Você não tem permissão para enviar orçamentos.")
    if orcamento.status in (Orcamento.CANCELADO, Orcamento.RECUSADO):
        raise RegraNegocioError("Orçamento cancelado ou recusado não pode ser enviado.")


def enviar_por_email(request, orcamento, omitir_ids=(), destino=None):
    """Envia o PDF da versão por e-mail. Falha (e-mail não configurado, endereço inválido, erro do provedor) => mensagem clara e registro;
    o orçamento continua salvo e na mesma situação."""
    from .pdf import gerar_pdf

    _exigir_pode_enviar(request, orcamento)
    destino = (destino or orcamento.destinatario_email or "").strip()
    try:
        validate_email(destino)
    except ValidationError:
        raise RegraNegocioError("Informe um e-mail válido do destinatário.") from None
    if not email_disponivel():
        raise RegraNegocioError("O envio de e-mail não está configurado neste ambiente.")
    versao = versao_para_envio(orcamento, omitir_ids)
    empresa = orcamento.cliente.nome_fantasia or orcamento.cliente.nome_razao_social
    corpo = (
        f"Olá, {orcamento.destinatario_nome}.\n\nSegue em anexo o orçamento nº {orcamento.numero} de {empresa}, no valor de "
        f"{DinheiroService.formatar(versao.totais.total)}" + (f", válido até {orcamento.validade:%d/%m/%Y}" if orcamento.validade else "") + ".\n\nEnviado por Faturando Certo."
    )
    try:
        mensagem = EmailMessage(f"Orçamento nº {orcamento.numero} - {empresa}"[:200], corpo, to=[destino])
        responder = (orcamento.cliente.email_contato or "").strip()
        if responder:
            mensagem.reply_to = [responder]
        mensagem.attach(f"orcamento-{orcamento.numero}.pdf", gerar_pdf(versao, request.user), "application/pdf")
        mensagem.send()
    except Exception:  # noqa: BLE001 (qualquer falha vira mensagem segura; o detalhe técnico vai só para o log do servidor)
        logger.exception("Falha ao enviar o orçamento %s por e-mail", orcamento.pk)
        registrar_envio(request, orcamento, OrcamentoEnvio.EMAIL, OrcamentoEnvio.FALHA, destino, "Falha ao gerar ou enviar o e-mail.", versao.omitidos)
        raise RegraNegocioError("Não foi possível enviar o e-mail agora. O orçamento continua salvo; tente novamente.") from None
    with transaction.atomic():
        _marcar_enviado(orcamento)
        registrar_envio(request, orcamento, OrcamentoEnvio.EMAIL, OrcamentoEnvio.OK, destino, "E-mail enviado.", versao.omitidos)
        registrar(request, acao="editar", modulo=MODULO, tabela=orcamento._meta.db_table, registro_id=orcamento.pk, registro_nome=str(orcamento),
                  descricao=f"Orçamento ID {orcamento.pk} enviado por e-mail (destino e conteúdo não registrados).", silencioso=False)
    return orcamento


def criar_link(orcamento, omitir_ids):
    return OrcamentoLink.objects.create(orcamento=orcamento, itens_omitidos=list(omitir_ids), expira_em=timezone.now() + VALIDADE_DO_LINK)


def enviar_por_whatsapp(request, orcamento, conexao_id, omitir_ids=(), contato=None):
    """Enfileira, na conexão de WhatsApp da UNIDADE, uma mensagem com o resumo e o link do PDF (os conectores só enviam texto). A mensagem
    entra na fila de saída (envio pelo agendador): "ok" aqui significa "na fila", e o resultado final fica em Integrações > Mensagens."""
    _exigir_pode_enviar(request, orcamento)
    if not request.user.has_perm("integracao.enviar_mensagem"):
        raise RegraNegocioError("Você não tem permissão para enviar mensagens pelas integrações.")
    try:
        conexao = conexoes_whatsapp(orcamento).get(pk=int(conexao_id))
    except (Conexao.DoesNotExist, TypeError, ValueError):
        raise RegraNegocioError("Escolha uma conexão de WhatsApp ativa da sua unidade.") from None
    contato = re.sub(r"[^\d+]", "", str(contato or orcamento.destinatario_whatsapp or ""))
    if len(contato) < 8:
        raise RegraNegocioError("Informe o WhatsApp do destinatário.")
    versao = versao_para_envio(orcamento, omitir_ids)
    base = (getattr(settings, "BASE_URL", "") or "").rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    empresa = orcamento.cliente.nome_fantasia or orcamento.cliente.nome_razao_social
    try:
        with transaction.atomic():
            link = criar_link(orcamento, versao.omitidos)
            texto = (
                f"Olá, {orcamento.destinatario_nome}! Orçamento nº {orcamento.numero} de {empresa}: total {DinheiroService.formatar(versao.totais.total)}"
                + (f", válido até {orcamento.validade:%d/%m/%Y}" if orcamento.validade else "") + f".\nPDF: {base}/orcamento/publico/{link.token}"
            )
            integracao.enfileirar_mensagem(request, conexao, contato, texto)
            _marcar_enviado(orcamento)
            registrar_envio(request, orcamento, OrcamentoEnvio.WHATSAPP, OrcamentoEnvio.OK, contato, f"Mensagem na fila da conexão {conexao.nome}.", versao.omitidos)
    except RegraNegocioError as erro:
        registrar_envio(request, orcamento, OrcamentoEnvio.WHATSAPP, OrcamentoEnvio.FALHA, contato, str(erro), versao.omitidos)
        raise
    return orcamento
