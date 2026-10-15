"""Registro dos provedores fiscais. Para um provedor novo (ex.: gateway de NFC-e/NF-e por UF): crie a classe, herde de `ProvedorFiscal`,
implemente `pronto`/`emitir`/`cancelar` e acrescente-a ao REGISTRO. O núcleo (services.py) não muda."""
from .base import ErroFiscal, ProvedorFiscal, ResultadoEmissao  # noqa: F401
from .nenhum import Nenhum
from .nfse_nacional import NfseNacional

REGISTRO = {p.chave: p for p in (Nenhum(), NfseNacional())}


def obter(chave):
    return REGISTRO.get(chave, REGISTRO["nenhum"])
