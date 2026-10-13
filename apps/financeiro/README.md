# apps/financeiro — Financeiro

Contas a pagar, contas a receber, categorias financeiras, **DRE** e **balanço** gerenciais e notificações de vencimento, por unidade (cliente matriz/filial).

```text
Usuário → Cliente → Segmento → Módulo (financeiro-pagar / -receber / -relatorios) habilitado → permissão → dados da unidade (escopo matriz/filial)
```

> **Os relatórios são gerenciais, não demonstrações contábeis oficiais.** Não há plano de contas, competência completa, depreciação, impostos sobre o lucro nem escrituração; as telas mostram esse aviso. Para fins contábeis e fiscais, use o contador.

## Contas e baixas

`ContaPagar` e `ContaReceber` (mesma estrutura, `ContaBase`): descrição, fornecedor/cliente, **categoria** (despesa para a pagar, receita para a receber), valor (centavos > 0), emissão, vencimento, observação.

- **Baixa** = pagamento/recebimento, **total ou parcial**, com valor, data (não pode ser futura), observação e responsável. `valor_baixado` é o total líquido; o banco recusa `valor_baixado > valor` (CheckConstraint) e `registrar_baixa` relê a conta sob bloqueio de linha (`select_for_update`), então duas baixas simultâneas não passam do saldo.
- **Histórico imutável** (`web_project/historico.py`): baixas não se editam nem se apagam, nem em lote. O erro se corrige com um **estorno**, que desfaz a baixa inteira e é um novo registro (`estorno_de`; cada baixa só é estornada uma vez; estorno não se estorna). A baixa original continua no histórico.
- **Situação** (calculada): em aberto, parcial, paga, cancelada; **atrasada** = com saldo e vencimento anterior a hoje.
- **Edição**: o valor não pode ficar abaixo do já baixado; conta cancelada não se edita.
- **Exclusão**: sem nenhum registro de baixa → apagada. Com baixas **ativas** → bloqueada ("estorne antes"). Com baixas todas estornadas → **cancelada** (`ativo=False`) e o histórico permanece. Cancelar nunca esconde dinheiro já movimentado.
- Criação é na unidade do usuário; a lista mostra filiais (selo + filtro) só com `cliente.view_itensfilial`; baixar/editar/excluir em filial exige `change_/delete_itensfilial` (`apps/acl/README.md`).

## Categorias financeiras

`CategoriaFinanceira` (por unidade; tipo receita/despesa; única por unidade+tipo+nome) agrupa o DRE. Cadastro na própria tela, **ativar/desativar** e excluir (em uso por contas → só desativa). "Criar categorias padrão" cria, **na unidade e só quando o usuário pede**, Vendas/Outras receitas e Fornecedores, Aluguel, Energia/água/internet, Salários, Impostos e Outras despesas (nada é copiado automaticamente).

## DRE gerencial (`services.dre`)

Por unidade e período (padrão: mês corrente). Receitas por categoria, (−) despesas por categoria, **resultado = receitas − despesas**.

| Regime | Receitas / despesas |
|---|---|
| **Caixa** (padrão) | recebimentos/pagamentos **líquidos** (baixas − estornos) com data **no período**, na categoria da conta |
| **Vencimento** | valor das contas **ativas** com vencimento no período (pagas ou não) |

Categorias sem movimento não aparecem; contas canceladas não entram no regime de vencimento. Período invertido → aviso e volta ao mês corrente.

## Balanço gerencial (`services.balanco`)

Quadro simplificado de **uma unidade por vez** (a do usuário; a matriz escolhe a filial com permissão) numa data (padrão hoje):

| Linha | Origem |
|---|---|
| **Caixa gerencial** | baixas líquidas das contas a receber − das contas a pagar, até a data. **Sem saldo inicial**: pode ficar negativo |
| **Contas a receber em aberto** | valor das contas ativas **emitidas até a data** − baixas líquidas até a data |
| **Estoque (a custo)** | saldo reconstruído pelo histórico de movimentações até a data (`estoque.services.valor_em_estoque`), valorizado ao **custo atual** de cada insumo (preço pago ÷ quantidade comprada) — não há histórico de preços |
| **Passivo** | contas a pagar em aberto (mesma regra) |
| **Patrimônio** | ativo − passivo (resíduo: inclui capital e saldos iniciais não lançados) |

## Notificações

`python manage.py gerar_alertas_financeiro [--cliente ID]` (agende diariamente) notifica, por unidade e por tipo (a pagar/receber × atrasadas/vencendo), as contas **ativas com saldo** atrasadas ou vencendo em até `settings.FINANCEIRO_DIAS_ALERTA_VENCIMENTO` dias (padrão 3), em **uma notificação-resumo** por unidade e tipo. Só usuários **da própria unidade** com permissão de ver aquele tipo de conta recebem. `AlertaFinanceiro` garante **uma notificação por conta por tipo por dia** (idempotente). As listas também mostram um aviso com as contagens. Usa `notificacao.services.enviar_para_usuarios`.

## Telas e permissões

| Rota | Permissão |
|---|---|
| `contas-pagar`, `contas-receber` (lista com filtros, totais, Excel/PDF) | `view_contapagar` / `view_contareceber` |
| `.../nova` (Salvar e adicionar outra) | `add_*` |
| `.../<id>` (detalhe, baixas) | `view_*` |
| `.../<id>/editar`, `/baixar`, `/baixas/<id>/estornar` | `change_*` |
| `.../<id>/excluir` | `delete_*` |
| `financeiro/dre`, `financeiro/balanco` | `view_relatoriofinanceiro` |
| `financeiro/categorias` (+ padrão, alternar, excluir) | `view/add/change/delete_categoriafinanceira` |

Listas: filtros por texto, categoria, situação, período de vencimento e unidade; ordenação por lista fechada; paginação; totais (valor, baixado, saldo) do filtro; exportação Excel/PDF do queryset filtrado/escopado (auditada). Na matriz de papéis: linhas "Contas a pagar", "Contas a receber", "DRE e Balanço" (só *Visualizar*) e "Categorias financeiras". `seed_acl`: Operador/Visualizador recebem as ações padrão das contas (view/add; view), mas **DRE/balanço só Administrador e Gestor** (`SOMENTE_AUTORIZADOS`).

## Banco de dados

- `financeiro_categoriafinanceira`: `cliente_id`, `nome`, `tipo`, `ativo`; único `(cliente, tipo, nome)`.
- `financeiro_contapagar` / `financeiro_contareceber`: `cliente_id`, `descricao`, `contraparte`, `categoria_id` (PROTECT), `valor` bigint (centavos), `emissao`, `vencimento`, `observacao`, `valor_baixado` bigint (≤ `valor`), `ativo`, `criado_em`, `atualizado_em`. Índice `(cliente, vencimento)`.
- `financeiro_baixapagar` / `financeiro_baixareceber`: `conta_id` (PROTECT), `tipo` (`baixa`/`estorno`), `valor`, `data`, `observacao`, `responsavel_id` + `responsavel_nome`, `estorno_de_id` (OneToOne, nulo), `criado_em`.
- `financeiro_alertafinanceiro`: `cliente_id`, `tipo`, `referencia` (id da conta), `data`; único por dia.

Migrations: `financeiro/0001` e `modulo/0008_modulo_financeiro` (cadastra os módulos, inclui nos segmentos padrão e habilita para clientes que já têm segmento, sem sobrescrever vínculos). Rode também `seed_acl`.

## Limitações

- Não há parcelamento automático (lance cada parcela como uma conta), conciliação bancária, contas bancárias/caixas separados nem recorrência.
- O balanço não tem saldo inicial, depreciação nem outros ativos/passivos; o estoque usa o custo atual, não o histórico.
- Contas não se movem sozinhas a partir de vendas, compras de insumos ou estoque (não há módulo de vendas).

Testes: `apps/financeiro/tests.py` (baixas parciais/totais, limites, estorno, imutabilidade, DRE por caixa e por vencimento, balanço, alertas e notificações idempotentes, telas, validações, filtros, exportação, permissões por tipo, tenant e matriz/filial, categorias).
