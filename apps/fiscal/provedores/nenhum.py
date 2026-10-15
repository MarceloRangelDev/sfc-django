from .base import ProvedorFiscal


class Nenhum(ProvedorFiscal):
    chave = "nenhum"
    rotulo = "Nenhum"
    tipos = ()

    def pronto(self, documento, configuracao):
        return False, "Nenhum provedor fiscal configurado: o documento permanece pendente e a venda segue com comprovante não fiscal."
