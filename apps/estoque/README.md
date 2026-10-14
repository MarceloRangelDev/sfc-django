# apps/estoque — Estoque

Controle de estoque por unidade (cliente matriz/filial): saldos por insumo e por **lote**, **validade**, entradas, saídas, ajustes, **transferências** entre matriz e filiais, **mínimo** por insumo e **alertas** de estoque baixo e vencimento. O estoque é alimentado pelos **Insumos cadastrados** (todo insumo ativo aparece com saldo 0 até a primeira entrada; processados entram por serem insumos).

```text
Usuário → Cliente → Segmento → Módulo "estoque" habilitado → permissão → dados da unidade (escopo matriz/filial)
```

## Princípios

- **Saldos separados por unidade**: cada unidade tem os próprios lotes e saldos. A matriz vê os das filiais (selo + filtro de unidade) só com `cliente.view_itensfilial`; a filial nunca vê a matriz (`apps/acl/README.md`). Registrar entrada/saída/ajuste e definir mínimo é na unidade do usuário.
- **Histórico protegido**: `MovimentoEstoque` e `TransferenciaEstoque` não se apagam e não se alteram em lote (`save()`/`delete()` e `update()`/`delete()` levantam `HistoricoImutavelError`). A **única** correção permitida é `services.editar_movimento` (ver "Edição de movimentação"), que usa `web_project/historico.py::corrigir_registro`/`corrigir_em_lote`, valida, recalcula saldos, grava `editado_em` e audita. Transferências só se desfazem por nova transferência.
- **Sem saldo negativo, mesmo com operações simultâneas**: cada movimento roda numa transação que **bloqueia a linha do lote** (`select_for_update`; em PostgreSQL serializa quem mexe no mesmo lote) e o banco recusa saldo < 0 (`CheckConstraint lote_saldo_nao_negativo`). `manage.py verificar_estoque [--corrigir]` confere cada saldo contra a soma do histórico (idempotente; sai com erro se houver divergência).
- **Quantidades na unidade do insumo** (`Insumo.medida`). O usuário pode informar outra unidade da **mesma dimensão** (500 g num insumo em kg); a conversão usa a tabela `medidas`. Dimensões diferentes são recusadas. Por isso um insumo **com estoque não pode trocar de unidade** (tela, API e a unidade de saída de processados), e **não pode ser excluído com saldo** (zere antes).

## Movimentos (`services.py`)

| Tipo | Efeito | Lote |
|---|---|---|
| Entrada / Ajuste (acréscimo) | soma | lote opcional; **validade pode ser informada sem lote** (usa o lote automático `VAL-AAAAMMDD`, que agrupa tudo que vence no dia, mantendo o PEPS); lote existente mantém sua validade (outra validade é recusada) |
| Saída / Ajuste (redução) | subtrai | **PEPS por validade**: consome primeiro o lote que vence antes; sem validade, o mais antigo; um movimento por lote tocado |
| Transferência enviada / recebida | subtrai na origem, soma no destino | acompanha a mercadoria (mesmo código e validade) |

Cada movimento guarda tipo, quantidade (sempre positiva), lote, **saldo do lote após**, responsável (conta + nome gravado), data e observação. Saída acima do saldo total é recusada, sem gravar nada.

## Transferências entre matriz e filiais

Só quem tem `estoque.add_transferenciaestoque` (padrão: **Administrador e Gestor**; Operador/Visualizador não). Regras (`services.transferir`, tudo ou nada):

- **origem = unidade do usuário**; destino = outra unidade ativa **da mesma estrutura** (matriz ↔ filiais ou filial ↔ filial). Uma filial envia à matriz, mas não retira dela.
- o insumo de destino é o de **mesmo nome** (e mesma dimensão) na unidade de destino; se não existir, a transferência é recusada com a explicação (nada é criado automaticamente).
- registra origem, destino, **responsável**, data, observação e os itens (um par saída/entrada por lote movimentado). Quem enxerga: a unidade de origem e a de destino (e, para a matriz, as filiais com permissão).

## Mínimo e alertas

- **Mínimo** (`EstoqueMinimo`, na unidade do insumo; 0 = sem alerta), definido por quem tem `change_movimentoestoque` e auditado.
- **Estoque baixo**: saldo < mínimo. **Vencimento**: lote **com saldo** vencido ou que vence em até `settings.ESTOQUE_DIAS_ALERTA_VENCIMENTO` dias (padrão 7). Aparecem na lista de saldos, na página **Alertas** e nos filtros "Abaixo do mínimo" / "Vencido / vencendo".
- **Notificações** (módulo `apps/notificacao`, `enviar_para_usuarios`): para os usuários **da própria unidade** com `view_movimentoestoque`. Disparadas (1) na hora, quando uma saída/transferência leva o saldo abaixo do mínimo, e (2) pelo comando `python manage.py gerar_alertas_estoque [--cliente ID]` (agende diariamente) com um resumo por unidade para baixo e vencimento. `AlertaEnviado` garante **uma notificação por item por dia** (idempotente). Falha de entrega nunca desfaz o movimento.

## Edição de movimentação (`services.editar_movimento`)

Pode mudar: **quantidade** (e a unidade em que foi digitada), **data** (não pode ser futura), **observação** e, nas entradas, a **validade**. Não muda: insumo, tipo, lote e a unidade do insumo. Movimentos de **transferência** não são editáveis.

```text
delta = sinal × (nova quantidade − quantidade antiga)        (entrada: +, saída: −)
saldo do lote           ← saldo + delta
"saldo após" de cada movimento POSTERIOR do mesmo lote ← + delta
```

- **Sem saldo negativo**: a edição é recusada se o saldo atual do lote, o "saldo após" do próprio movimento **ou o de qualquer movimento posterior** ficasse < 0 (o histórico mostraria um estoque impossível). Exemplo: entrada de 10 e saída de 4 no mesmo lote → reduzir a entrada para 3 é recusado; aumentá-la para 12 deixa o saldo em 8.
- **Concorrência**: numa transação, `select_for_update` no movimento, no lote e nos movimentos posteriores (em PostgreSQL serializa edições/saídas simultâneas; `CheckConstraint lote_saldo_nao_negativo` é a rede de segurança).
- **Validade** só muda se o lote tem **uma única movimentação** (senão mudaria a validade de outras entradas); em lote automático o código `VAL-...` acompanha a nova validade e conflito com outro lote é recusado. Omitir = manter; vazio/`null` = remover.
- **Tenant e permissão**: tela/API exigem `change_movimentoestoque`; movimento de outra unidade (fora do escopo `clientes_dos_itens(..., "change")`) = 404.
- **Auditoria**: `registrar_alteracoes` com antes/depois dos campos mudados (quantidade, data, observação, validade); a lista mostra "Editada em ...".

## Telas e permissões

| Rota | Permissão | O que faz |
|---|---|---|
| `estoque` | `view_movimentoestoque` | Saldos por insumo: filtros (pesquisa, situação, unidade), ordenação, paginação, **Excel/PDF** |
| `estoque/insumo/<id>` | `view_movimentoestoque` | Lotes (saldo/validade) e últimas movimentações |
| `estoque/movimentar` | `add_movimentoestoque` | Entrada, saída ou ajuste (**Salvar** e **Salvar e registrar outra**) |
| `estoque/insumo/<id>/minimo` | `change_movimentoestoque` | Define o mínimo (também por um atalho na coluna "Mínimo" da lista de saldos, mostrado só nas unidades que o usuário pode alterar) |
| `estoque/movimentos` | `view_movimentoestoque` | Histórico: filtros (texto, tipo, período, unidade), paginação, **Excel/PDF**; lápis para editar |
| `estoque/movimentos/<id>/editar` | `change_movimentoestoque` | Corrige a movimentação e recalcula os saldos |
| `estoque/alertas` | `view_movimentoestoque` | Estoque baixo e vencimentos |
| `estoque/transferencias`, `.../<id>` | `view_transferenciaestoque` | Lista e detalhe |
| `estoque/transferencias/nova` | `add_transferenciaestoque` | Registra a transferência |

As permissões do módulo cabem na matriz de papéis assim: linha **Estoque** (`*_movimentoestoque`: ver = consultar tudo; criar = movimentar; editar = definir mínimo; excluir = sem uso, o histórico não se apaga) e linha **Transferências de estoque** (`*_transferenciaestoque`). `seed_acl` não dá as de transferência a Operador/Visualizador (`SOMENTE_AUTORIZADOS`). Exportações usam o queryset filtrado/escopado da tela e são auditadas.

## Banco de dados

- `estoque_lote`: `cliente_id`, `insumo_id`, `codigo` (vazio = sem lote), `validade` (nulo), `saldo` decimal(15,4) (≥ 0, CheckConstraint). Único `(cliente, insumo, codigo)`.
- `estoque_movimentoestoque`: `cliente_id`, `insumo_id`, `lote_id`, `tipo`, `quantidade` (> 0), `saldo_lote_apos`, `responsavel_id` + `responsavel_nome`, `transferencia_id` (nulo), `observacao`, `data`, `criado_em`, `editado_em` (nulo; preenchido na edição — migration `0002`, reversível). Índice `(cliente, insumo, data)`.
- `estoque_transferenciaestoque`: `origem_id`, `destino_id` (CheckConstraint origem ≠ destino), `responsavel_id` + `responsavel_nome`, `observacao`, `data`, `criado_em`.
- `estoque_estoqueminimo`: `insumo_id` (um por insumo), `minimo` decimal(15,4).
  - **Fase 3** (migration `estoque.0003_fator_reposicao_e_multiplo`, aditiva): `fator_reposicao` decimal(6,2) NOT NULL, padrão `2` (>= 1) e `multiplo_compra` decimal(15,4) NULL (> 0). Alimentam a lista de compras (`apps/compras`). Linhas existentes ficam com fator 2 e sem múltiplo; nada é recalculado. Alterações são auditadas junto com o mínimo.
- `estoque_alertaenviado`: `cliente_id`, `tipo` (`baixo`/`vencimento`), `referencia` (id do insumo/lote), `data`. Único por dia.

Migrations: `estoque/0001` e `modulo/0007_modulo_estoque` (cadastra "estoque", inclui nos segmentos padrão e habilita para clientes que já têm segmento, sem sobrescrever vínculos; e a linha "Transferências de estoque" da matriz de papéis).

## Etiquetas com QR Code (`etiquetas.py`)

Aba **Etiquetas** (`estoque/etiquetas`, permissão `estoque.view_movimentoestoque`) e API `GET /api/v1/estoque/saldos/{insumo}/etiqueta/`. Gera PDF com nome, lote (opcional), emissão, validade e QR:
- **Formatos:** `a4` (grade 3 × 8 de 63,5 × 33,9 mm) e `termica` (50 × 30 mm, uma por página); 1 a 200 etiquetas.
- **Validade:** o usuário escolhe a cada geração — `digitada` (obrigatória, não anterior à emissão) ou `calculada` (emissão + `Insumo.dias_validade`; erro se o insumo não tem dias).
- **QR:** contém só `<BASE_URL>/i/<uuid do insumo>` (`BASE_URL` do `.env`; sem ela, o host da requisição). O UUID é aleatório e estável (`Insumo.uuid`). A rota `/i/<uuid>` exige login, módulo de estoque e o mesmo tenant: de outra empresa ou UUID inexistente → 404 idêntico; anônimo → login e volta ao insumo. Sem preço, lote ou dado pessoal no código.
- Cada geração é auditada (`exportar`). O QR usa o gerador nativo do reportlab (sem dependência nova). A conferência visual do layout deve ser feita imprimindo uma folha de teste.

## Limitações conhecidas

- **Saída de insumo processado não baixa as matérias-primas** (estoque do processado e dos insumos são independentes); venda de produto não baixa estoque (não há módulo de vendas).
- O bloqueio de linha (`select_for_update`) só tem efeito em PostgreSQL; em SQLite (desenvolvimento) as gravações já são serializadas. A suíte valida a regra de saldo e o `CheckConstraint`, mas **não exercita transações paralelas reais**.
- Movimentar estoque de uma filial (pela matriz) não é permitido; a matriz acompanha e transfere.

Testes: `apps/estoque/tests.py` (entradas/saídas/PEPS/ajustes, validade sem lote, edição de movimentação com saldos/negativo/tenant/auditoria, imutabilidade, constraint, conferência, transferências e seus erros, alertas e notificações idempotentes, telas, filtros, exportação, permissões, tenant, matriz/filial).
