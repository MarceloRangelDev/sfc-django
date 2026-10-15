"""Contrato dos provedores fiscais. Um provedor transforma uma venda em documento fiscal AUTORIZADO pela autoridade competente.

REGRA DE OURO: o sistema nunca inventa autorização. `emitir` só devolve `ResultadoEmissao` com `autorizada=True` quando a resposta REAL do
serviço fiscal traz chave de acesso e protocolo. Provedor sem integração concluída deve declarar `pronto()` falso com o motivo, e o
documento fica PENDENTE (documentado), nunca "emitido".
"""
from dataclasses import dataclass, field


class ErroFiscal(Exception):
    def __init__(self, mensagem, temporario=False):
        super().__init__(mensagem)
        self.temporario = temporario


@dataclass
class ResultadoEmissao:
    autorizada: bool
    rejeitada: bool = False
    chave_acesso: str = ""
    protocolo: str = ""
    numero: int | None = None
    serie: int | None = None
    xml: str = ""
    mensagem: str = ""
    extras: dict = field(default_factory=dict)


class ProvedorFiscal:
    chave = ""
    rotulo = ""
    tipos = ()  # tipos de documento que o provedor emite

    def pronto(self, documento, configuracao):
        """(ok, motivo): se o provedor consegue emitir AGORA este documento."""
        return False, "Provedor não implementado."

    def emitir(self, documento, configuracao, certificado):
        raise ErroFiscal("Emissão não implementada neste provedor.")

    def cancelar(self, documento, configuracao, certificado, motivo):
        raise ErroFiscal("Cancelamento não implementado neste provedor.")
