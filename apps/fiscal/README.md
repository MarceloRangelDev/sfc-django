# Fiscal (`apps/fiscal`) — ESTRUTURA PRONTA, EMISSÃO REAL AINDA NÃO DISPONÍVEL

> **Este módulo NÃO emite nota fiscal.** Ele prepara a empresa, guarda o certificado com segurança, registra o pedido de emissão e mantém uma fila.
> Um documento só vira **AUTORIZADO** com chave de acesso e protocolo reais devolvidos pela autoridade fiscal — e nenhum provedor real está concluído.
> Nada é simulado: sem provedor pronto, o documento fica **PENDENTE** com o motivo, e a venda segue com o **comprovante NÃO FISCAL** do PDV.

## O que existe e funciona

| Item | Estado |
|---|---|
| Cadastro fiscal da empresa: regime tributário (MEI, Simples, Presumido, Real) e "emite nota fiscal" no cliente (`cliente_cliente.regime_tributario`, `emite_nota_fiscal`) | pronto |
| Configuração por unidade (`fiscal_configuracaofiscal`): ambiente (homologação/produção), provedor, tipos habilitados (NFC-e, NF-e, NFS-e), inscrições, código IBGE, série | pronto |
| **Certificado A1 (.pfx)**: aberto de verdade (senha, titular, validade; vencido é recusado), guardado **cifrado** (Fernet, `INTEGRACAO_CRYPTO_KEYS`), nunca exibido, nunca em log/auditoria; sem chave de cifra em produção o sistema recusa gravar | pronto |
| Lista de pendências por tipo ("o que falta") na tela e na API | pronto |
| Pedido de documento por venda **paga** (idempotente, um ativo por venda e tipo), cancelamento com motivo (≥ 15 caracteres) | pronto |
| Fila de pendentes com reprocessamento (`manage.py processar_fiscal`), tentativas e espera crescente em erro temporário | pronto |
| Auditoria de configuração, certificado (só o fato, sem segredo), pedido e cancelamento | pronto |
| Contrato de provedores (`provedores/base.py`) para plugar um emissor real sem mexer no núcleo | pronto |

## O que NÃO existe (e por quê)

Nenhum provedor está concluído. O **Emissor Nacional de NFS-e** (`provedores/nfse_nacional.py`) valida os requisitos e para: faltam, e dependem de insumos que o sistema não tem e não deve inventar:

1. layout oficial da DPS e esquemas XSD da versão vigente do Padrão Nacional;
2. assinatura digital XMLDSig (C14N) com o certificado A1;
3. comunicação HTTPS com autenticação mútua nos ambientes de homologação e produção;
4. credenciamento da empresa e **certificado de homologação** para validar autorização, rejeição, consulta e cancelamento.

**NFC-e e NF-e** são estaduais (SEFAZ de cada UF, com CSC/token e regras próprias): exigem um provedor por UF ou um gateway contratado. **DANFE/cupom fiscal** só pode ser gerado a partir de documento autorizado — por isso não é gerado.

## Estados do documento

`PENDENTE` → (provedor pronto e emissão) → `AUTORIZADA` · `REJEITADA` · `ERRO`; `CANCELADA` (autorizada só com cancelamento real no provedor). `AUTORIZADA` sem chave e protocolo é recusada e vira `ERRO`.

## Permissões

`fiscal.view_documentofiscal` (ver), `fiscal.emitir_nota` (pedir, processar fila, cancelar) e `fiscal.change_configuracaofiscal` (configurar e enviar certificado). Padrão: **Administrador e Gestor**; Operador e Visualizador só por permissão especial por usuário. Tenant: tudo da unidade do usuário (outra unidade: 404).

## API v1

`GET /fiscal/configuracao/` (sem segredos), `GET/POST /fiscal/documentos/` (POST idempotente), `GET /fiscal/documentos/{id}/`, `POST /fiscal/documentos/{id}/cancelar/`. O certificado **não** trafega pela API.

## Tabelas

`fiscal_configuracaofiscal`, `fiscal_documentofiscal` (migração `fiscal.0001`) e as colunas `cliente_cliente.regime_tributario`, `cliente_cliente.emite_nota_fiscal` (`cliente.0009`). Todas aditivas.

## Para a emissão real

Escolher o provedor por tipo, obter credenciamento e certificado de homologação, implementar a classe do provedor (contrato em `base.py`), homologar com casos de autorização, rejeição, consulta e cancelamento, e só então mudar o ambiente para produção. Este README e a tela permanecem avisando até lá.
