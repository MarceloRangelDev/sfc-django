"""Fiscal: prontidão da empresa, certificado digital A1, pedido de documento fiscal e fila de pendentes.

NADA AQUI SIMULA AUTORIZAÇÃO. Um documento só vira AUTORIZADO quando um provedor fiscal devolve chave de acesso e protocolo reais
(ver provedores/base.py). Sem provedor concluído, o pedido fica PENDENTE com o motivo, e a venda continua com o comprovante NÃO FISCAL.

Certificado A1 (.pfx): conferido de verdade (abre o arquivo, lê titular e validade, recusa vencido/senha errada) e guardado CIFRADO junto
com a senha (apps/integracao/cripto.py); nunca é exibido nem registrado em log/auditoria.
"""
import base64
from datetime import timedelta

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auditoria.utils import registrar, registrar_criacao
from apps.integracao import cripto
from apps.pdv.models import Venda
from web_project.regras import RegraNegocioError

from .models import ConfiguracaoFiscal, DocumentoFiscal
from .provedores import ErroFiscal, obter

MAX_TAMANHO_PFX = 64 * 1024
MAX_TENTATIVAS = 5
AVISO_VENCIMENTO_DIAS = 30


# --------------------------------------------------------------------------
# Certificado digital A1
# --------------------------------------------------------------------------

def salvar_certificado(request, configuracao, arquivo, senha):
    """Valida o .pfx (abre com a senha, lê titular e validade, recusa vencido) e guarda cifrado. Devolve a configuração."""
    if not arquivo or len(arquivo) > MAX_TAMANHO_PFX:
        raise RegraNegocioError("Envie o arquivo .pfx/.p12 do certificado A1 (até 64 KB).")
    try:
        chave, certificado, _ = pkcs12.load_key_and_certificates(arquivo, (senha or "").encode())
    except (ValueError, TypeError):
        raise RegraNegocioError("Não foi possível abrir o certificado: confira o arquivo e a senha.") from None
    if chave is None or certificado is None:
        raise RegraNegocioError("O arquivo não contém certificado e chave privada.")
    validade = certificado.not_valid_after_utc
    if validade < timezone.now():
        raise RegraNegocioError("O certificado está vencido.")
    try:
        titular = certificado.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    except IndexError:
        titular = certificado.subject.rfc4514_string()
    try:
        cifrado = cripto.cifrar({"pfx": base64.b64encode(arquivo).decode(), "senha": senha or ""})
    except cripto.CriptoIndisponivel as erro:
        raise RegraNegocioError(str(erro)) from None
    with transaction.atomic():
        configuracao.certificado_cifrado, configuracao.certificado_validade, configuracao.certificado_assunto = cifrado, validade, titular[:255]
        configuracao.save(update_fields=["certificado_cifrado", "certificado_validade", "certificado_assunto", "atualizado_em"])
        registrar(request, acao="editar", modulo="fiscal", tabela=configuracao._meta.db_table, registro_id=configuracao.pk, registro_nome=str(configuracao),
                  descricao=f"Certificado digital substituído (titular {titular[:80]}, válido até {validade:%d/%m/%Y}). Arquivo e senha não são registrados.", silencioso=False)
    return configuracao


def remover_certificado(request, configuracao):
    with transaction.atomic():
        configuracao.certificado_cifrado, configuracao.certificado_validade, configuracao.certificado_assunto = "", None, ""
        configuracao.save(update_fields=["certificado_cifrado", "certificado_validade", "certificado_assunto", "atualizado_em"])
        registrar(request, acao="editar", modulo="fiscal", tabela=configuracao._meta.db_table, registro_id=configuracao.pk, registro_nome=str(configuracao),
                  descricao="Certificado digital removido.", silencioso=False)


def certificado_de(configuracao):
    """(pfx_bytes, senha) decifrados, ou None. Só para uso interno do provedor (nunca vai a tela/log)."""
    if not configuracao.certificado_cifrado:
        return None
    try:
        dados = cripto.decifrar(configuracao.certificado_cifrado)
    except (cripto.CriptoIndisponivel, ValueError):
        raise ErroFiscal("Não foi possível ler o certificado (chave de cifra ausente ou diferente da usada ao enviá-lo). Envie-o novamente.") from None
    return base64.b64decode(dados["pfx"]), dados["senha"]


# --------------------------------------------------------------------------
# Prontidão
# --------------------------------------------------------------------------

def requisitos(cliente, tipo):
    """Lista do que falta para emitir `tipo` (vazia = pronto do ponto de vista cadastral; o PROVEDOR ainda diz se consegue emitir)."""
    configuracao = ConfiguracaoFiscal.da_unidade(cliente)
    faltando = []
    if not cliente.emite_nota_fiscal:
        faltando.append("A empresa não optou por emitir nota fiscal (cadastro do cliente).")
        return faltando
    if not cliente.regime_tributario:
        faltando.append("Regime tributário (MEI, Simples Nacional, Lucro Presumido ou Lucro Real) não informado.")
    if tipo not in configuracao.tipos_habilitados():
        faltando.append(f"O tipo {dict(DocumentoFiscal.TIPO_CHOICES).get(tipo, tipo)} não está habilitado.")
    if not configuracao.tem_certificado:
        faltando.append("Certificado digital A1 não enviado.")
    elif configuracao.certificado_validade and configuracao.certificado_validade < timezone.now():
        faltando.append("Certificado digital vencido.")
    if tipo == DocumentoFiscal.NFSE:
        if not configuracao.codigo_municipio:
            faltando.append("Código IBGE do município não informado.")
        if not configuracao.inscricao_municipal:
            faltando.append("Inscrição municipal não informada.")
    elif not configuracao.inscricao_estadual:
        faltando.append("Inscrição estadual não informada.")
    if configuracao.provedor == ConfiguracaoFiscal.PROVEDOR_NENHUM:
        faltando.append("Nenhum provedor fiscal selecionado.")
    return faltando


def prontidao(cliente):
    """{tipo: [pendências]} para os três tipos (tela de configuração)."""
    return {t: requisitos(cliente, t) for t, _ in DocumentoFiscal.TIPO_CHOICES}


# --------------------------------------------------------------------------
# Documentos
# --------------------------------------------------------------------------

def solicitar_emissao(request, venda, tipo):
    """Pede o documento fiscal da venda PAGA. Cria PENDENTE (com o motivo do que falta). IDEMPOTENTE: já existindo um ativo do mesmo tipo,
    devolve-o. Devolve (documento, criado). A venda e o comprovante não fiscal NÃO mudam."""
    if tipo not in dict(DocumentoFiscal.TIPO_CHOICES):
        raise RegraNegocioError("Tipo de documento inválido.")
    cliente = venda.cliente
    if venda.status != Venda.PAGA:
        raise RegraNegocioError("Só vendas pagas podem gerar documento fiscal.")
    if not cliente.emite_nota_fiscal:
        raise RegraNegocioError("A empresa não optou por emitir nota fiscal.")
    existente = DocumentoFiscal.objects.filter(venda=venda, tipo=tipo, status__in=[DocumentoFiscal.PENDENTE, DocumentoFiscal.PROCESSANDO, DocumentoFiscal.AUTORIZADA]).first()
    if existente:
        return existente, False
    configuracao = ConfiguracaoFiscal.da_unidade(cliente)
    motivo = " ".join(requisitos(cliente, tipo)) or "Aguardando processamento pelo provedor fiscal."
    try:
        with transaction.atomic():
            documento = DocumentoFiscal.objects.create(
                cliente=cliente, venda=venda, tipo=tipo, ambiente=configuracao.ambiente, motivo=motivo[:500], proxima_tentativa=timezone.now(),
                criado_por=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            )
            registrar_criacao(request, documento, modulo="fiscal")
    except IntegrityError:
        existente = DocumentoFiscal.objects.filter(venda=venda, tipo=tipo, status__in=[DocumentoFiscal.PENDENTE, DocumentoFiscal.PROCESSANDO, DocumentoFiscal.AUTORIZADA]).first()
        if existente:
            return existente, False
        raise
    return documento, True


def processar_pendentes(limite=50, agora=None, clientes=None):
    """Percorre os documentos PENDENTES: se o provedor ainda não está pronto, só atualiza o motivo (não gasta tentativa); se está, tenta
    emitir. Resultado AUTORIZADO exige chave de acesso e protocolo reais devolvidos pelo provedor. Devolve contadores."""
    agora = agora or timezone.now()
    resumo = {"autorizadas": 0, "rejeitadas": 0, "aguardando": 0, "erros": 0}
    fila = DocumentoFiscal.objects.filter(status=DocumentoFiscal.PENDENTE, proxima_tentativa__lte=agora)
    if clientes is not None:
        fila = fila.filter(cliente__in=clientes)
    for pk in list(fila.order_by("proxima_tentativa", "pk").values_list("pk", flat=True)[:limite]):
        with transaction.atomic():
            documento = DocumentoFiscal.objects.select_for_update().select_related("cliente", "venda").filter(pk=pk, status=DocumentoFiscal.PENDENTE).first()
            if documento is None:
                continue
            configuracao = ConfiguracaoFiscal.da_unidade(documento.cliente)
            provedor = obter(configuracao.provedor)
            falta = requisitos(documento.cliente, documento.tipo)
            ok, motivo = provedor.pronto(documento, configuracao)
            if falta or not ok:
                documento.motivo = " ".join(falta) if falta else motivo
                documento.proxima_tentativa = agora + timedelta(hours=1)
                documento.save(update_fields=["motivo", "proxima_tentativa", "atualizado_em"])
                resumo["aguardando"] += 1
                continue
            documento.tentativas += 1
            documento.status = DocumentoFiscal.PROCESSANDO
            documento.save(update_fields=["tentativas", "status", "atualizado_em"])
            try:
                resultado = provedor.emitir(documento, configuracao, certificado_de(configuracao))
            except ErroFiscal as erro:
                definitivo = (not erro.temporario) or documento.tentativas >= MAX_TENTATIVAS
                documento.status = DocumentoFiscal.ERRO if definitivo else DocumentoFiscal.PENDENTE
                documento.motivo = str(erro)[:500]
                documento.proxima_tentativa = agora + timedelta(minutes=min(5 * 2 ** documento.tentativas, 360))
                documento.save(update_fields=["status", "motivo", "proxima_tentativa", "atualizado_em"])
                resumo["erros"] += 1
                continue
            if resultado.autorizada:
                if not (resultado.chave_acesso and resultado.protocolo):
                    documento.status, documento.motivo = DocumentoFiscal.ERRO, "O provedor não devolveu chave de acesso e protocolo: não é possível considerar a nota autorizada."
                    documento.save(update_fields=["status", "motivo", "atualizado_em"])
                    resumo["erros"] += 1
                    continue
                documento.status, documento.chave_acesso, documento.protocolo = DocumentoFiscal.AUTORIZADA, resultado.chave_acesso, resultado.protocolo
                documento.numero, documento.serie, documento.xml_autorizado, documento.autorizado_em, documento.motivo = resultado.numero, resultado.serie, resultado.xml, agora, ""
                resumo["autorizadas"] += 1
            else:
                documento.status, documento.motivo = DocumentoFiscal.REJEITADA, (resultado.mensagem or "Rejeitada pelo provedor.")[:500]
                resumo["rejeitadas"] += 1
            documento.save()
    return resumo


def cancelar_documento(request, documento, motivo):
    """Pendente/erro/rejeitado: cancela só localmente. Autorizado: exige o provedor (cancelamento real); sem suporte, recusa."""
    motivo = " ".join((motivo or "").split())
    if len(motivo) < 15:
        raise RegraNegocioError("Informe o motivo do cancelamento (mínimo 15 caracteres, como exige a legislação).")
    with transaction.atomic():
        documento = DocumentoFiscal.objects.select_for_update().select_related("cliente").get(pk=documento.pk)
        if documento.status == DocumentoFiscal.CANCELADA:
            raise RegraNegocioError("O documento já está cancelado.")
        if documento.status == DocumentoFiscal.AUTORIZADA:
            configuracao = ConfiguracaoFiscal.da_unidade(documento.cliente)
            try:
                obter(configuracao.provedor).cancelar(documento, configuracao, certificado_de(configuracao), motivo)
            except ErroFiscal as erro:
                raise RegraNegocioError(f"O cancelamento junto à autoridade não foi concluído: {erro}") from None
        antes = documento.get_status_display()
        documento.status, documento.motivo = DocumentoFiscal.CANCELADA, motivo[:500]
        documento.save(update_fields=["status", "motivo", "atualizado_em"])
        registrar(request, acao="editar", modulo="fiscal", tabela=documento._meta.db_table, registro_id=documento.pk, registro_nome=str(documento),
                  descricao=f"Documento fiscal ID {documento.pk} cancelado (era {antes}). Motivo: {motivo[:200]}", silencioso=False)
    return documento
