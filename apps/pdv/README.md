# PDV (caixa, vendas, comandas e comprovante)

Ponto de venda integrado ao estoque, ao financeiro e à auditoria. O comprovante é **NÃO FISCAL**; a emissão fiscal é outro fluxo (`apps/fiscal`, Fase 9) e só acontece com provedor configurado e confirmado.

## Como o PDV decide preço e ficha

O PDV vende **produtos** cuja **Precificação** está marcada **"Usar no PDV"** (no máximo uma, ativa, por produto; marcar uma desmarca as outras, com auditoria). Dela vêm o **preço** (`preco_venda`) e a **ficha técnica** usada na baixa de estoque. O PDV é um canal próprio: não depende de `CanalVenda` (que segue opcional). O app/tela só envia *produto + quantidade*; **preço, ficha, totais, taxas e troco são calculados no servidor**. Cada item e pagamento guarda um **snapshot** (nome, preço, ficha, taxa): mudar cadastros depois não altera vendas antigas.

## Perfis

| Ação | Operador | Gestor | Administrador |
|---|---|---|---|
| Vender (balcão/comanda), ver **as próprias** vendas | sim | sim | sim |
| Abrir e fechar o **próprio** caixa (`abrir_caixa`) | sim | sim | sim |
| Ver todas as vendas/caixas da unidade | — | sim | sim |
| Cancelar venda (`cancelar_venda`), devolver itens (`devolver_item`) | — | sim | sim |
| Sangria (`sangria`), suprimento (`suprimento`), conferir fechamento (`conferir_caixa`), conceder desconto (`conceder_desconto`) | — | sim | sim |
| Formas de pagamento, mesas e ajustes (Configurações) | — | ver/criar/editar | tudo |

As permissões próprias do PDV ficam fora da matriz de Papéis; para outros usuários use a permissão especial por usuário. A migration `pdv.0001` cria e concede essas permissões aos perfis já existentes.

## Caixa

Um **turno por operador** (restrição no banco: um caixa aberto por operador), na unidade do operador (matriz ou filial). Abre com o **troco inicial**; **sangria/suprimento** são lançamentos imutáveis (sangria não pode passar do dinheiro disponível). O **dinheiro esperado** = troco inicial + vendas em dinheiro + suprimentos − sangrias − devoluções em dinheiro (vendas canceladas não entram). No **fechamento** o operador informa o dinheiro contado; o sistema guarda esperado, contado e **diferença**, e quem tem `conferir_caixa` **confere** depois. Não fecha com comanda aberta.

## Venda

- **Pagamentos:** de 1 a 10 por venda (formas mistas e **conta dividida**: a tela divide em 2–4 partes iguais, o resto de centavos vai para as primeiras; cada parte aceita um *pagador*). A soma deve ser **exatamente** o total. **Troco** só em dinheiro (`valor_recebido ≥ valor`).
- **Taxa por forma de pagamento:** escolhida em *Configurações > Formas de pagamento*: **sem taxa**, **percentual**, **valor fixo por transação** ou **ambos**. Taxa de um pagamento = `round_half_up(valor × % / 100) + fixa` (nunca passa do valor). Gravada no pagamento (snapshot) e mostrada como líquido nos relatórios do caixa. A taxa é **informativa**: o financeiro registra a receita pelo valor bruto (não gera despesa de taxa).
- **Desconto:** em centavos, até o subtotal, exige `conceder_desconto`; o total precisa ser > 0.
- **Estoque:** a baixa ocorre **uma vez** (`baixa_estoque_em`) na finalização, pelos insumos da ficha técnica × quantidade vendida (PEPS do estoque; movimentos com a observação *Venda PDV #n*). O que saiu fica em `VendaItemInsumo` (rastreabilidade). Saldo insuficiente **recusa a venda inteira** (nada é gravado). O insumo gerado por um Processado é um insumo comum (baixa direta nele; a receita do processado não é reaberta). Desligue em *Configurações do PDV* ("Dar baixa no estoque ao vender") para negócios sem controle de insumos.
- **Financeiro:** a venda paga gera uma **conta a receber** (categoria *Vendas PDV*, criada na primeira venda) já **baixada**, uma baixa por pagamento.
- **Numeração:** sequencial por unidade (bloqueio da unidade evita repetição).
- **Idempotência:** `chave` por tentativa (tela: gerada no navegador; API: campo `chave` ou cabeçalho `Idempotency-Key`). Repetir a mesma chave devolve a **mesma venda** (`criada: false`) sem duplicar estoque, conta ou numeração. Se a rede cair, a tela mantém a chave e o reenvio é seguro.
- **Consumidor** opcional (da mesma unidade, ativo e não anonimizado).

## Comandas e mesas

Uma comanda é uma venda **aberta** ligada a uma **mesa** (uma comanda aberta por mesa, garantido no banco). Itens podem ser lançados/removidos enquanto aberta (preço do servidor; mesmo produto soma); nada é baixado nem recebido até **receber** (conta dividida = vários pagamentos). A baixa de estoque da comanda acontece ao receber (na Fase 6, pedidos de cozinha podem baixar na confirmação; a baixa nunca se repete).

## Cancelamento e devolução

- **Cancelar** (venda aberta ou paga, motivo obrigatório): repõe o estoque dos itens **não preparados**, estorna as baixas e inativa a conta a receber. Venda paga só com o **caixa dela ainda aberto**; depois use a devolução. Não cancela venda com devolução registrada.
- **Devolver itens** (venda paga): valor proporcional ao item **com o desconto rateado**; o estoque só é reposto se o responsável pedir **e o item não foi preparado** (`preparado`, marcado pela cozinha na Fase 6; hoje padrão falso). Devolução em dinheiro exige **caixa aberto do usuário** e sai do esperado. Gera uma **conta a pagar** já baixada (*Devoluções de vendas*). Devolução é **imutável**.

## Comprovante (todas as opções de impressão)

`pdv/vendas/<id>/comprovante` — página HTML **imprimível** em qualquer dispositivo (botão *Imprimir* do navegador), com CSS para **80 mm**, **58 mm** e **A4**; `?formato=pdf` (recibo contínuo ou A4); `?formato=escpos` (bytes ESC/POS cp850 para impressora térmica via agente/app de impressão). Mostra *COMPROVANTE NAO FISCAL*. O layout foi gerado e testado por software, **não em hardware de impressão**.

## API (`/api/v1`, mobile)

| Método | Rota | Descrição | Permissão |
|---|---|---|---|
| GET/POST/PUT/PATCH/DELETE | `/formas-pagamento/`, `/{id}/` | Formas e taxa (`taxa_modo`: `sem_taxa`/`percentual`/`fixa`/`ambas`; `taxa_fixa` em centavos) | `pdvconfig.*_formapagamento` |
| GET/POST/... | `/mesas/`, `/{id}/` | Mesas | `pdvconfig.*_mesa` |
| GET/PATCH | `/pdv/configuracao/` | Ajustes do PDV da unidade | `pdvconfig.view/change_configuracaopdv` |
| GET | `/pdv/produtos/?q=` | Produtos vendáveis e preço | `pdv.add_venda` |
| GET | `/pdv/caixa/atual/`, `/pdv/caixa/`, `/pdv/caixa/{id}/` | Caixa aberto, histórico e detalhe (com resumo) | `pdv.view_caixa` |
| POST | `/pdv/caixa/abrir/`, `/fechar/`, `/sangria/`, `/suprimento/`, `/{id}/conferir/` | Operações de caixa | `abrir_caixa`, `sangria`, `suprimento`, `conferir_caixa` |
| GET/POST | `/pdv/vendas/`, `/pdv/vendas/{id}/` | Lista (`status`, `q`), cria venda de balcão (**idempotente**), detalhe | `view_venda` / `add_venda` |
| POST | `/pdv/vendas/{id}/cancelar/`, `/pdv/vendas/{id}/itens/{item}/devolver/` | Cancelar e devolver | `cancelar_venda`, `devolver_item` |
| GET | `/pdv/vendas/{id}/comprovante/?formato=json\|pdf\|escpos&tamanho=` | Comprovante | `view_venda` |
| GET/POST | `/pdv/comandas/`, `/pdv/comandas/{id}/` | Comandas abertas / abrir / detalhe | `view_venda` / `add_venda` |
| POST/DELETE | `/pdv/comandas/{id}/itens/`, `/itens/{item}/` | Lançar/remover item | `add_venda` |
| POST | `/pdv/comandas/{id}/pagar/` | Receber (conta dividida; idempotente) | `add_venda` |

Erros: 400 validação/caixa não aberto, 401, 403, 404 (outra unidade ou venda de outro operador), 422 regra de negócio (soma dos pagamentos, estoque, caixa fechado...). Dinheiro em **centavos**. Atenção ao `API_THROTTLE_USER` (padrão 2000/h) para um caixa movimentado.

## Tenant e auditoria

Todo acesso passa por `clientes_dos_itens` (unidade do usuário e, para a matriz com `cliente.*_itensfilial`, as filiais); quem não tem `cancelar_venda` só enxerga **as próprias** vendas. Audita-se: abertura/fechamento/conferência de caixa, sangria/suprimento, criação/recebimento da venda (com itens, pagamentos e totais), lançamento/remoção de itens da comanda, cancelamento (motivo), devolução, criação das contas e categorias financeiras automáticas e as configurações.

## Banco de dados

Migrations `pdv.0001`/`0002` (tabelas novas; concedem permissões), `pdvconfig.0001` (tabelas novas), `precificacao.0006` (coluna `usar_no_pdv`) e `modulo.0014` (módulos `pdv`, `forma-pagamento`, `mesa`, `pdv-config`). Nenhum dado existente é alterado.

| Tabela | Colunas principais |
|---|---|
| `pdv_caixa` | `cliente_id` FK, `operador_id` FK, `status` (aberto/fechado; único aberto por operador), `valor_abertura`, `aberto_em`, `fechado_em` (nulo), `valor_esperado`/`valor_contado`/`diferenca` (nulos até fechar), `observacao_fechamento`, `conferido_por_id`/`conferido_em` (nulos), `observacao_conferencia` |
| `pdv_movimentocaixa` | `caixa_id`, `tipo` (sangria/suprimento), `valor`, `motivo`, `usuario_id`, `criado_em` — **imutável** |
| `pdv_venda` | `cliente_id`, `numero` (único por unidade), `caixa_id`, `consumidor_id` (nulo), `mesa_id` (nulo; única aberta por mesa), `origem`, `status`, `subtotal`, `desconto` (≤ subtotal), `total`, `troco`, `chave_idempotencia` (nula; única por unidade), `baixa_estoque_em`, `conta_receber_id` (nulo), `observacao`, `criado_por_id`, `finalizada_em`, `cancelada_em`/`cancelada_por_id`/`motivo_cancelamento` |
| `pdv_vendaitem` | `venda_id`, `produto_id`, `ficha_id`, `descricao`, `quantidade` decimal(12,3), `preco_unitario`, `total`, `quantidade_devolvida` (≤ quantidade), `preparado`, `observacao` |
| `pdv_vendapagamento` | `venda_id`, `forma_id`, `forma_nome`, `forma_tipo`, `valor`, `valor_recebido` (nulo), `taxa_valor`, `taxa_descricao`, `pagador` |
| `pdv_vendaiteminsumo` | `item_id`, `insumo_id`, `medida_id`, `quantidade` baixada, `quantidade_reposta` (≤ baixada) |
| `pdv_devolucao` | `venda_id`, `item_id`, `quantidade`, `valor`, `repor_estoque`, `forma_estorno_id`, `caixa_id` (nulo), `motivo`, `usuario_id`, `conta_pagar_id` (nulo), `criado_em` — **imutável** |
| `precificacao_precificacao.usar_no_pdv` | bool, padrão falso; único por produto quando `usar_no_pdv` e `ativo` |

Valores em centavos (bigint). Índices em (`cliente`, `status`) de caixa e venda e em `venda.caixa`.


## Catálogo do PDV: nada some em silêncio (atualizado)

O catálogo (`services.produtos_do_pdv`) mostra precificações **marcadas para o PDV, ativas, de produto e ficha ativos e com preço > 0**, da unidade do caixa. Causas de “marquei e não aparece” investigadas: (1) a tela cortava em **80 produtos sem avisar** — agora mostra até 300 e avisa “Mostrando X de Y” (`truncado`/`total` no JSON); a API do catálogo (`/api/v1/pdv/produtos/`) passou a ser **paginada** (`?pagina=&por_pagina=`, padrão 100, máx. 200; a lista continua em `data.itens`); (2) a busca só olhava o nome — agora também a **categoria** e o código; (3) a lista de **Precificações** não mostrava o status do PDV — ganhou a coluna *PDV* (“No PDV” ou “Marcada, mas fora do PDV” + motivo: precificação/produto/ficha inativos ou preço zero; também em `motivo_fora_do_pdv` na API). Nenhuma regra de autorização foi enfraquecida (continua só a unidade do caixa).

**Formas de pagamento globais** liberadas ao segmento também valem no pagamento, na devolução e na tela (ver `apps/pdvconfig/README.md`). **Serviços** (ficha só com mão de obra) vendem sem baixar estoque.
