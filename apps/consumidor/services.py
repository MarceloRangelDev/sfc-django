"""Regras do módulo Consumidores: anonimização (LGPD) e consultas externas de CEP e CNPJ.

Consultas externas (decisão do cliente): CEP pelo ViaCEP e CNPJ pela BrasilAPI, ambos gratuitos e sem
chave. CPF NÃO tem consulta pública legítima: só validação dos dígitos (apps/cliente/validators.py).

Regras de segurança das consultas:
- só dígitos validados (CEP 8, CNPJ com dígitos verificadores) entram na URL — nunca texto livre (sem SSRF);
- o resultado NUNCA é gravado automaticamente: é uma sugestão para pré-preencher o formulário, que o
  usuário confere e salva (e o model valida de novo). Dado de terceiro pode estar errado ou defasado;
- timeout curto, tamanho máximo da resposta, cache de 1 hora e limite por usuário (rate limit);
- indisponibilidade do provedor vira `ConsultaIndisponivel` (a tela segue funcionando com digitação manual);
- o CNPJ consultado é enviado ao provedor (dado público de empresa); nenhum dado do usuário/tenant é enviado.
"""
import json
import logging
import re
import urllib.error
import urllib.request

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import ACAO_EXCLUIR
from apps.auditoria.utils import registrar
from apps.cliente.validators import validar_cnpj
from auth.ratelimit import check_rate_limit, hit

logger = logging.getLogger(__name__)

URL_CEP = "https://viacep.com.br/ws/{cep}/json/"
URL_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
TIMEOUT_SEGUNDOS = 5
TAMANHO_MAXIMO_RESPOSTA = 200_000  # bytes
CACHE_SEGUNDOS = 3600
LIMITE_CONSULTAS = 30  # por usuário por minuto
AVISO = "Dados de fonte externa: confira antes de salvar; podem estar incorretos ou desatualizados."


class ConsultaErro(Exception):
    """Base dos erros de consulta externa (mensagem segura para o usuário)."""


class ConsultaInvalida(ConsultaErro):
    """CEP/CNPJ mal formado."""


class ConsultaNaoEncontrada(ConsultaErro):
    pass


class ConsultaIndisponivel(ConsultaErro):
    pass


class ConsultaLimite(ConsultaErro):
    def __init__(self, espera):
        super().__init__("Muitas consultas. Tente novamente em instantes.")
        self.espera = espera


def _buscar_json(url):
    """GET JSON com timeout e teto de tamanho. Único ponto que acessa a rede (os testes o substituem)."""
    pedido = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "SFC/1.0"})
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:  # noqa: S310 (URL fixa https)
            bruto = resposta.read(TAMANHO_MAXIMO_RESPOSTA + 1)
    except urllib.error.HTTPError as erro:
        if erro.code in (400, 404):
            raise ConsultaNaoEncontrada("Nada encontrado para o valor informado.") from None
        raise ConsultaIndisponivel("O serviço de consulta está indisponível no momento.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConsultaIndisponivel("O serviço de consulta está indisponível no momento.") from None
    if len(bruto) > TAMANHO_MAXIMO_RESPOSTA:
        raise ConsultaIndisponivel("Resposta inesperada do serviço de consulta.")
    try:
        dados = json.loads(bruto)
    except ValueError:
        raise ConsultaIndisponivel("Resposta inesperada do serviço de consulta.") from None
    if not isinstance(dados, dict):
        raise ConsultaIndisponivel("Resposta inesperada do serviço de consulta.")
    return dados


def _limitar(usuario):
    """Rate limit por usuário (cache compartilhado do Django)."""
    identificador = str(usuario.pk)
    estado = check_rate_limit("consumidor-consulta", identificador, LIMITE_CONSULTAS, 60)
    if estado.blocked:
        raise ConsultaLimite(estado.retry_after)
    hit("consumidor-consulta", identificador, 60)


def _texto(valor, maximo):
    return str(valor or "").strip()[:maximo]


def consultar_cep(usuario, cep):
    """Endereço sugerido para o CEP (ViaCEP): {cep, logradouro, bairro, cidade, uf, aviso}."""
    cep = re.sub(r"\D", "", str(cep or ""))
    if len(cep) != 8:
        raise ConsultaInvalida("O CEP deve ter 8 dígitos.")
    _limitar(usuario)
    chave = f"consulta-cep:{cep}"
    resultado = cache.get(chave)
    if resultado is None:
        dados = _buscar_json(URL_CEP.format(cep=cep))
        if dados.get("erro"):  # o ViaCEP responde 200 com {"erro": true} para CEP inexistente
            raise ConsultaNaoEncontrada("CEP não encontrado.")
        resultado = {
            "cep": cep, "logradouro": _texto(dados.get("logradouro"), 150), "bairro": _texto(dados.get("bairro"), 100),
            "cidade": _texto(dados.get("localidade"), 100), "uf": _texto(dados.get("uf"), 2).upper(),
        }
        cache.set(chave, resultado, CACHE_SEGUNDOS)
    return {**resultado, "aviso": AVISO}


def consultar_cnpj(usuario, cnpj):
    """Dados cadastrais públicos sugeridos para o CNPJ (BrasilAPI). Só o necessário ao cadastro."""
    cnpj = re.sub(r"\D", "", str(cnpj or ""))
    if not validar_cnpj(cnpj):
        raise ConsultaInvalida("CNPJ inválido.")
    _limitar(usuario)
    chave = f"consulta-cnpj:{cnpj}"
    resultado = cache.get(chave)
    if resultado is None:
        dados = _buscar_json(URL_CNPJ.format(cnpj=cnpj))
        resultado = {
            "documento": cnpj, "nome": _texto(dados.get("razao_social"), 150),
            "nome_fantasia": _texto(dados.get("nome_fantasia"), 150),
            "cep": re.sub(r"\D", "", str(dados.get("cep") or ""))[:8], "logradouro": _texto(dados.get("logradouro"), 150),
            "numero": _texto(dados.get("numero"), 20), "complemento": _texto(dados.get("complemento"), 100),
            "bairro": _texto(dados.get("bairro"), 100), "cidade": _texto(dados.get("municipio"), 100),
            "uf": _texto(dados.get("uf"), 2).upper(),
            "telefone": re.sub(r"\D", "", str(dados.get("ddd_telefone_1") or ""))[:11],
            "situacao": _texto(dados.get("descricao_situacao_cadastral"), 40),
        }
        cache.set(chave, resultado, CACHE_SEGUNDOS)
    return {**resultado, "aviso": AVISO}


CAMPOS_PESSOAIS = (
    "documento", "email", "telefone", "whatsapp", "instagram", "cep", "logradouro", "numero", "complemento",
    "bairro", "cidade", "uf",
)


def anonimizar_consumidor(request, consumidor):
    """LGPD: apaga os dados pessoais e desativa o consumidor, mantendo o registro (id) para não
    quebrar vendas/pedidos que o referenciem. Irreversível. Na MESMA transação grava a auditoria,
    que identifica o item só por ID (o nome antigo não é repetido). Atenção: lançamentos de auditoria
    ANTERIORES (criação/edição) guardam o nome da época; apague-os conforme a política de retenção."""
    with transaction.atomic():
        if consumidor.anonimizado_em:
            return consumidor
        for campo in CAMPOS_PESSOAIS:
            setattr(consumidor, campo, "")
        consumidor.nome = f"Consumidor anonimizado #{consumidor.pk}"
        consumidor.tipo_pessoa = consumidor.TIPO_PF
        consumidor.data_nascimento = None
        consumidor.consentimento_lgpd = False
        consumidor.consentimento_em = None
        consumidor.ativo = False
        consumidor.anonimizado_em = timezone.now()
        consumidor.save()
        registrar(
            request, acao=ACAO_EXCLUIR, modulo="consumidor", tabela=consumidor._meta.db_table,
            registro_id=str(consumidor.pk), registro_nome=consumidor.nome,
            descricao=f"Consumidor ID {consumidor.pk} anonimizado (LGPD): dados pessoais apagados.",
            silencioso=False,
        )
    return consumidor
