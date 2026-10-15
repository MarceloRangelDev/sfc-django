"""Emissor Nacional de NFS-e (nfse.gov.br): ESTRUTURA PRONTA, EMISSÃO AINDA NÃO IMPLEMENTADA.

O que já existe: verificação dos requisitos (regime, município, inscrição municipal, certificado válido, ambiente) e o ponto de integração
(`emitir`). O que FALTA para concluir (depende de insumos que o sistema não tem e não deve inventar):
  1. o layout oficial da DPS (Declaração de Prestação de Serviço) e os esquemas XSD da versão vigente do Padrão Nacional;
  2. assinatura digital XMLDSig do documento com o certificado A1 da empresa (canonicalização C14N);
  3. comunicação HTTPS com autenticação mútua (certificado de cliente) nos endpoints de homologação e produção do Ambiente de Dados Nacional;
  4. credenciamento da empresa e um CERTIFICADO DE HOMOLOGAÇÃO para validar o fluxo (autorização, rejeição, consulta e cancelamento).
Sem isso qualquer "emissão" seria simulação e é proibida. Por isso `pronto()` informa o que falta e o documento fica PENDENTE.
"""
from datetime import datetime, timezone

from .base import ErroFiscal, ProvedorFiscal


class NfseNacional(ProvedorFiscal):
    chave = "nfse_nacional"
    rotulo = "Emissor Nacional (NFS-e)"
    tipos = ("nfse",)

    def pronto(self, documento, configuracao):
        faltando = []
        if documento.tipo not in self.tipos:
            return False, "O Emissor Nacional emite apenas NFS-e (NFC-e e NF-e são estaduais e exigem outro provedor)."
        if not configuracao.tem_certificado:
            faltando.append("certificado digital A1")
        elif configuracao.certificado_validade and configuracao.certificado_validade < datetime.now(timezone.utc):
            faltando.append("certificado digital válido (o atual venceu)")
        if not configuracao.codigo_municipio:
            faltando.append("código IBGE do município")
        if not configuracao.inscricao_municipal:
            faltando.append("inscrição municipal")
        if faltando:
            return False, "Falta configurar: " + ", ".join(faltando) + "."
        return False, ("Integração com o Emissor Nacional ainda não concluída: aguarda o layout da DPS, a assinatura XMLDSig e a homologação com certificado "
                       "de teste. O documento permanece pendente.")

    def emitir(self, documento, configuracao, certificado):
        raise ErroFiscal("Emissão pelo Emissor Nacional ainda não implementada.")
