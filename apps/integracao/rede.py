"""Acesso à rede das integrações: único ponto que faz requisições externas (os testes o substituem).

Proteções: só `https` (exceto localhost em DEBUG), host que resolve para endereço PÚBLICO (bloqueia SSRF: loopback, rede privada,
link-local), timeout curto, teto de tamanho da resposta e cabeçalhos/credenciais nunca vão para log.
"""
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

TIMEOUT = 10
MAX_RESPOSTA = 1_000_000


class ErroDeRede(Exception):
    def __init__(self, mensagem, status=None, espera=None, temporario=True, detalhe=""):
        super().__init__(mensagem)
        # `detalhe`: texto de erro devolvido pelo provedor (ex.: motivo da Meta recusar um template). Só chega a quem
        # o pedir explicitamente; a mensagem padrão continua genérica. Nunca contém o que NÓS enviamos (token).
        self.status, self.espera, self.temporario, self.detalhe = status, espera, temporario, detalhe


def url_segura(url):
    """Valida a URL (https + host público). Devolve a URL ou levanta ValueError com mensagem segura."""
    partes = urllib.parse.urlsplit(url or "")
    local = settings.DEBUG and partes.hostname in ("localhost", "127.0.0.1")
    if partes.scheme != "https" and not local:
        raise ValueError("A URL precisa usar https.")
    if not partes.hostname or partes.username or partes.password:
        raise ValueError("URL inválida.")
    if local:
        return url
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise ValueError("Não foi possível resolver o endereço.") from None
    for endereco in enderecos:
        ip = ipaddress.ip_address(endereco)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("O endereço aponta para uma rede interna e não é permitido.")
    return url


def _detalhe_do_erro(erro):
    """Mensagem do provedor no corpo do erro HTTP (formato Graph API: {"error": {"error_user_msg"|"message"}}). Vazio se ilegível."""
    try:
        corpo = json.loads(erro.read(4096) or b"{}")
        interno = corpo.get("error") if isinstance(corpo, dict) else None
        interno = interno if isinstance(interno, dict) else {}
        return str(interno.get("error_user_msg") or interno.get("message") or "")[:300]
    except (ValueError, OSError):
        return ""


def requisitar(metodo, url, cabecalhos=None, corpo=None, json_resposta=True):
    """Faz a requisição e devolve (status, cabecalhos, dados). `corpo`: dict (JSON) ou bytes. Erros HTTP viram ErroDeRede
    (429/5xx temporários, com `espera` do Retry-After; 4xx definitivos)."""
    dados = None
    cabecalhos = dict(cabecalhos or {})
    if isinstance(corpo, (dict, list)):
        dados = json.dumps(corpo).encode()
        cabecalhos.setdefault("Content-Type", "application/json")
    elif isinstance(corpo, str):
        dados = corpo.encode()
    elif corpo is not None:
        dados = corpo
    pedido = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT) as resposta:  # noqa: S310 (URL já validada por url_segura/URLs fixas https)
            bruto = resposta.read(MAX_RESPOSTA + 1)
            status, heads = resposta.status, dict(resposta.headers)
    except urllib.error.HTTPError as erro:
        espera = erro.headers.get("Retry-After") if erro.headers else None
        raise ErroDeRede(f"O provedor respondeu HTTP {erro.code}.", status=erro.code, espera=int(espera) if (espera or "").isdigit() else None,
                         temporario=erro.code == 429 or erro.code >= 500, detalhe=_detalhe_do_erro(erro)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ErroDeRede("Não foi possível falar com o provedor.", temporario=True) from None
    if len(bruto) > MAX_RESPOSTA:
        raise ErroDeRede("Resposta grande demais.", temporario=False)
    if not json_resposta or not bruto:
        return status, heads, bruto
    try:
        return status, heads, json.loads(bruto)
    except ValueError:
        raise ErroDeRede("Resposta inesperada do provedor.", temporario=False) from None
