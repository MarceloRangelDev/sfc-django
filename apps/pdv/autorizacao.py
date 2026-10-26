"""Autorização no PDV por SENHA ou por CÓDIGO DE CRACHÁ (barras/QR): excluir item lançado, limpar a venda, cancelar venda/comanda,
devolver (extornar) itens e conceder desconto.

Quem executa a ação informa a credencial de alguém COM a permissão da operação (o próprio usuário, se a tem, ou um supervisor):
  - senha: usuário + senha (o usuário vazio = o logado); ou
  - código: o texto lido do crachá (gerado em `gerar_codigo`, guardado só como HMAC).
O servidor:
  1. valida a credencial (nunca é gravada nem logada);
  2. exige que o autorizador esteja ativo, tenha a permissão e alcance a unidade da venda (ou seja administrador global);
  3. limita tentativas erradas por usuário (anti força bruta) — senha e código contam no mesmo limite;
  4. registra na auditoria QUEM autorizou.
Mensagens de erro são genéricas: não revelam se o usuário/código existe nem o que falhou.
"""
import hashlib
import hmac
import secrets

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.db import transaction

from apps.auditoria.models import ACAO_EDITAR
from apps.auditoria.utils import registrar
from web_project.acesso import administrador_global, clientes_acessiveis
from web_project.regras import RegraNegocioError

from .models import CodigoAutorizacao

MAX_FALHAS, JANELA_SEGUNDOS = 5, 900
NEGADO = "Senha ou código de autorização inválido, ou sem permissão para esta operação."
# Qualquer das permissões de autorização libera o usuário a gerar o próprio crachá.
PERMISSOES_DE_AUTORIZACAO = ("pdv.conceder_desconto", "pdv.cancelar_venda", "pdv.remover_item", "pdv.devolver_item")


def _hash_do_codigo(codigo):
    # HMAC (e não hash salgado) porque a leitura precisa achar o dono pelo código; o código tem entropia alta (60 bits).
    return hmac.new(settings.SECRET_KEY.encode(), (codigo or "").strip().upper().encode(), hashlib.sha256).hexdigest()


def gerar_codigo(usuario):
    """Gera (e devolve UMA vez) o código de crachá do usuário; o anterior deixa de valer."""
    codigo = secrets.token_hex(8).upper()  # 16 hex = 64 bits
    with transaction.atomic():
        CodigoAutorizacao.objects.update_or_create(usuario=usuario, defaults={"codigo_hash": _hash_do_codigo(codigo)})
    return codigo


def credenciais_de(dados):
    """Extrai {usuario, senha, codigo} de um dict (corpo JSON/POST), tolerando tipos errados."""
    dados = dados if isinstance(dados, dict) else {}
    return {k: (str(dados.get(k) or "")[:128]) for k in ("usuario", "senha", "codigo")}


def autorizar(request, permissao, cliente, usuario="", senha="", codigo=""):
    """Devolve o usuário autorizador ou levanta RegraNegocioError. `usuario` vazio = o próprio usuário logado."""
    chave = f"pdv:autorizacao:{request.user.pk}"
    if cache.get(chave, 0) >= MAX_FALHAS:
        raise RegraNegocioError("Muitas tentativas de autorização. Aguarde alguns minutos.")
    autorizador = None
    if (codigo or "").strip():
        registro = CodigoAutorizacao.objects.select_related("usuario").filter(codigo_hash=_hash_do_codigo(codigo)).first()
        autorizador = registro.usuario if registro else None
    elif senha:
        autorizador = authenticate(request, username=(usuario or request.user.get_username()).strip(), password=senha)
    permitido = bool(
        autorizador is not None and autorizador.is_active and autorizador.has_perm(permissao)
        and (administrador_global(autorizador) or clientes_acessiveis(autorizador).filter(pk=cliente.pk).exists())
    )
    if not permitido:
        cache.add(chave, 0, JANELA_SEGUNDOS)
        cache.incr(chave)
        raise RegraNegocioError(NEGADO)
    cache.delete(chave)
    return autorizador


def auditar(request, autorizador, acao, venda):
    """Trilha da autorização (ator = quem executou; autorizador no texto). Sem senha nem código."""
    registrar(
        request, ACAO_EDITAR, modulo="pdv", registro_id=venda.pk if venda is not None else "", tabela="pdv_venda",
        registro_nome=f"Venda #{venda.numero}" if venda is not None else "Venda em andamento",
        descricao=f"Autorização por credencial: {acao}. Autorizado por {autorizador.get_username()}.", silencioso=False,
    )


def exigir_desconto(request, cliente, desconto, credenciais, venda=None):
    """Concessão de desconto: SEMPRE exige credencial de quem tem `pdv.conceder_desconto` (inclusive para quem já tem a permissão),
    tanto pela tela quanto pela API. Sem desconto, nada é pedido. Devolve o autorizador (ou None) para o chamador registrar."""
    try:
        desconto = int(desconto or 0)
    except (TypeError, ValueError):
        raise RegraNegocioError("Desconto inválido.") from None
    if desconto <= 0:
        return None
    c = credenciais_de(credenciais)
    if not (c["senha"] or c["codigo"]):
        raise RegraNegocioError("O desconto exige a autorização (senha ou código) de quem tem permissão para concedê-lo.")
    autorizador = autorizar(request, "pdv.conceder_desconto", cliente, c["usuario"], c["senha"], c["codigo"])
    auditar(request, autorizador, f"conceder desconto de R$ {desconto / 100:.2f}".replace(".", ","), venda)
    return autorizador
