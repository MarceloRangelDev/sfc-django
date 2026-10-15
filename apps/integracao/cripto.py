"""Cifra das credenciais das integrações (tokens, senhas de app, segredos de webhook).

- Algoritmo: Fernet (AES-128-CBC + HMAC-SHA256), biblioteca `cryptography`.
- Chave: variável de ambiente `INTEGRACAO_CRYPTO_KEYS` (uma ou mais chaves Fernet separadas por vírgula; a PRIMEIRA cifra,
  todas decifram — é assim que se faz a ROTAÇÃO: coloque a chave nova na frente, rode `manage.py rotacionar_credenciais` e
  depois remova a antiga). Gere uma com `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- Em DESENVOLVIMENTO (DEBUG=True) sem a variável, uma chave é derivada da SECRET_KEY (conveniência, não para produção).
  Em produção sem a variável o sistema RECUSA gravar credenciais (nunca guarda segredo em texto claro).
- Credenciais nunca são exibidas depois de gravadas, nem escritas em log, auditoria ou mensagem de erro.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class CriptoIndisponivel(Exception):
    """Sem chave configurada (produção): não se grava credencial."""


def _chaves():
    bruto = getattr(settings, "INTEGRACAO_CRYPTO_KEYS", "") or ""
    chaves = [k.strip() for k in bruto.split(",") if k.strip()]
    if chaves:
        return chaves
    if settings.DEBUG:
        derivada = base64.urlsafe_b64encode(hashlib.sha256(("integracao:" + settings.SECRET_KEY).encode()).digest())
        return [derivada.decode()]
    raise CriptoIndisponivel("Configure INTEGRACAO_CRYPTO_KEYS para guardar credenciais de integrações.")


def _fernet():
    return MultiFernet([Fernet(k.encode()) for k in _chaves()])


def cifrar(dados):
    """dict -> texto cifrado (str)."""
    return _fernet().encrypt(json.dumps(dados, separators=(",", ":")).encode()).decode()


def decifrar(texto):
    """texto cifrado -> dict ({} se vazio). Levanta ValueError se a chave não confere."""
    if not texto:
        return {}
    try:
        return json.loads(_fernet().decrypt(texto.encode()).decode())
    except InvalidToken:
        raise ValueError("Não foi possível decifrar as credenciais (chave diferente?).") from None


def recifrar(texto):
    """Recifra com a chave principal (rotação)."""
    return _fernet().rotate(texto.encode()).decode()
