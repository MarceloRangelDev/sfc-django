# apps/custo — Custo operacional

Custos **fixos** (aluguel, internet...) e **variáveis** (água, energia...) de cada unidade (cliente matriz/filial). O sistema **distribui sozinho, em reais (R$), o custo operacional total entre as categorias**, proporcionalmente às vendas de cada uma; o usuário pode **ajustar** o valor de uma categoria. Dali sai o custo operacional **por unidade vendida**, que preenche a Precificação.

```text
Usuário → Cliente → Segmento → Módulo "custo-operacional" habilitado → permissão custo.<ação>_custooperacional → CustoOperacional (escopo de unidades)
```

## Distribuição automática por categoria (`calculos.py::distribuir_por_vendas`, `services.py::distribuicao_por_categoria`)

```text
custo operacional total   = soma dos custos ATIVOS da unidade (valores mensais, em centavos)
custo da categoria        = total × (vendas da categoria ÷ vendas de todas as categorias elegíveis)
custo operacional/unidade = custo da categoria ÷ quantidade vendida da categoria
```

**Exemplo do negócio**: água + luz + gás = R$ 300,00 e três categorias que vendem 100 itens cada → R$ 100,00 por categoria (R$ 1,00 por item), total R$ 300,00. Com vendas 200 / 100 / 0 → R$ 200,00 / R$ 100,00 / R$ 0,00 (não se divide só pelo número de categorias).

Regras (todas testadas):

- **Período e fonte de vendas**: não existe módulo de vendas; a base é a `Categoria.quantidade_vendida`, que o usuário informa **por mês** (o mesmo período dos custos mensais). Essa é a lacuna conhecida: o "período" é o mês que o usuário digitou, não um histórico consultado pelo sistema.
- **Arredondamento (maior resto)**: cada categoria recebe o piso da sua fatia em centavos e os centavos que sobram vão, um a um, para quem teve o maior resto (empate: maior volume, depois menor id). **A soma das categorias é sempre exatamente o total.**
- **Categoria sem vendas** recebe R$ 0,00 (e o custo por unidade é 0, sem divisão por zero). **Nenhuma categoria com vendas** (sem histórico): o total é dividido **igualmente** entre as categorias ativas e a tela avisa.
- **Categorias inativas** não recebem rateio automático; só aparecem se têm ajuste manual.
- **Sem categoria para receber** (todas ajustadas ou nenhuma ativa): a sobra aparece como "não distribuído".
- **Ajuste manual** (`CustoAjusteCategoria`, em centavos): a categoria recebe exatamente o valor ajustado e o restante é redistribuído entre as demais (pelas vendas). O ajuste é **preservado**: não muda quando custos ou vendas mudam; só some quando o usuário clica em "Restaurar" (calculado). O sistema recusa um ajuste maior que o total ou que deixe a soma sem fechar quando não sobra categoria automática; se, depois, o total cair abaixo dos ajustes, a tela mostra o excedente. Ajustes e restaurações são **auditados** (antes/depois, "calculado" × "ajuste manual").
- **Uma única fonte de verdade**: a distribuição **nunca é gravada** — é calculada a cada consulta a partir de custos, vendas e ajustes (só os ajustes ficam no banco). Assim ela nunca diverge do custo cadastrado.
- **Precificação**: `services.custo_operacional_da_categoria` entrega o valor por unidade; a precificação o usa sozinha e as que **não foram ajustadas à mão** são refeitas quando custos, vendas ou ajustes mudam (sinais em `apps/precificacao/apps.py`). Ver `apps/precificacao/README.md`.

A distribuição **por percentual de cada custo** (versão anterior) foi substituída; a migration `0004` converteu as distribuições existentes em ajustes manuais, para ninguém perder a decisão. A tabela `custo_custorateio` continua no banco, sem uso (legado), para não perder dados nem o rollback.

## Custos comuns (Configurações > Custos comuns)

`CustoModelo` guarda os custos comuns usados como atalho no Custo operacional. Dois tipos de linha: **padrão do sistema** (`cliente` nulo; a migration `0002` carrega Aluguel, Condomínio, Água, Energia elétrica, Gás, Internet, Telefone, Contabilidade, Sistemas e assinaturas, Material de limpeza; só o admin do Django os altera, e os clientes os veem em somente leitura) e **próprios da empresa** (`cliente` preenchido), com CRUD completo na tela e na API. Nada é copiado para as unidades: a unidade só passa a ter o custo quando o **adiciona** informando o **próprio valor** (o banco garante `unique(cliente, modelo)` em `CustoOperacional`).

- Listagem com filtros: **pesquisa** (nome/descrição), **tipo** (fixo/variável), **origem** (padrão do sistema/próprios) e **status** (ativos por padrão); **ordenação** por nome, tipo ou data (padrão: nome); paginação (10/25/50/100) e exportação Excel/PDF.
- **Exclusão**: custo comum sem uso é apagado; se já foi usado em algum custo operacional, é **apenas desativado** (os custos e relatórios continuam intactos), com confirmação (SweetAlert2) e auditoria.
- Nome único entre os padrões do sistema e os da própria empresa (restrições `custo_comum_nome_global` e `custo_comum_nome_por_cliente`).
- A matriz com `cliente.view/change/delete_itensfilial` enxerga e gerencia os das filiais; filial nunca vê os da matriz.
- **Padrões do sistema** (`cliente` nulo) valem para todas as empresas: só o **administrador do sistema** (superusuário ou grupo "Administrador", `web_project/acesso.py::administrador_global`) os edita e exclui (um padrão em uso só é desativado). Para os demais usuários os botões Editar/Excluir aparecem apenas nos custos da própria empresa, conforme as permissões `change`/`delete_customodelo` (o Gestor padrão não tem `delete`); o servidor aplica a mesma regra (`views.py::custos_comuns_gerenciaveis`, também usada pela API).

## Nome do custo

No formulário, o campo **Nome** só aparece com **"Custo próprio (sem modelo)"**. Escolhendo um custo comum, o campo some e o nome (e o tipo) passam a ser os do custo comum — o servidor aplica a mesma regra (o nome enviado é ignorado) e, ao editar, o nome do modelo é mantido.

## Telas (`module_required("custo-operacional", ...)`)

| Rota | Permissão | O que faz |
|---|---|---|
| `custo-operacional` | `view_custooperacional` | Lista paginada: pesquisa, tipo (fixo/variável), status, unidade, ordenação, totais de fixos/variáveis/geral do filtro, exportação **Excel/PDF** (mesmo queryset filtrado/escopado; auditada). |
| `custo-operacional/novo` | `add_custooperacional` | Cadastro (aceita `?modelo=<id>`); **Salvar** e **Salvar e adicionar outro**. |
| `custo-operacional/<id>/editar` | `change_custooperacional` | Edição do custo (auditada). |
| `custo-operacional/<id>/excluir` (POST) | `delete_custooperacional` | Exclusão. |
| `custo-operacional/categorias` | `view_custooperacional` | Distribuição em R$ por categoria de **uma unidade por vez** (a do usuário; a matriz escolhe a filial com `cliente.view_itensfilial`), com origem (vendas / divisão igual / ajuste manual). |
| `custo-operacional/categorias/<id>/ajustar` (POST) | `change_custooperacional` | Fixa (`valor`) ou restaura (`restaurar=1`) o valor da categoria. |
| `custos-comuns`, `/novo`, `/<id>/editar`, `/<id>/excluir` | `view/add/change/delete_customodelo` (módulo `custo-comum`) | CRUD dos custos comuns (ver acima). |

Escopo de unidades e permissões de filial: `apps/acl/README.md`. Criação é na unidade do usuário.

## Banco de dados

`custo_customodelo`: `cliente_id` (FK PROTECT, **nulo = padrão do sistema**), `nome`, `tipo`, `descricao`, `ordem`, `ativo`, `criado_em`, `atualizado_em` (nulos nas linhas antigas). Único `nome` entre os padrões e único `(cliente, nome)` entre os próprios (migration `0003`: a coluna `nome` deixou de ser `unique` global).

`custo_custoajustecategoria`: `cliente_id` (FK PROTECT), `categoria_id` (FK PROTECT — categoria com ajuste só é desativada), `valor` bigint (**centavos**), `atualizado_em`. Único `(cliente, categoria)`. Só guarda as decisões do usuário.

`custo_custooperacional`: `cliente_id` (FK PROTECT), `nome` (único com cliente), `tipo` (`fixo`/`variavel`), `valor` bigint (**centavos, mensal**), `descricao`, `modelo_id` (FK PROTECT, nulo; único por cliente quando preenchido), `ativo`, `criado_em`, `atualizado_em`. Índice `(cliente, ativo)`.

`custo_custorateio` (**legado, sem uso**): `custo_id`, `categoria_id`, `percentual` decimal(7,4). Mantida para não perder dados; seus valores foram convertidos em ajustes pela migration `0004`.

Migrations: `0001` (tabelas), `0002` (catálogo global, idempotente), `0003` (custo comum por cliente, constraints novas e `custo_custoajustecategoria`), `0004` (rateios por percentual → ajustes manuais em R$; reversível, o rollback só apaga os ajustes criados), `modulo/0006_modulo_custo_operacional` (módulo de negócio) e `modulo/0009_modulo_custo_comum` (módulo essencial "Custos comuns" em Configurações).

Testes: `apps/custo/tests.py` (exemplo de R$ 300 em 3 categorias, volumes diferentes, centavos residuais, maior resto, sem vendas, ajuste manual e restauração, auditoria, custos comuns CRUD/filtros/escopo/permissões, telas, exportação, matriz/filial e a sugestão na Precificação) e `apps/api/tests_operacao.py`.

## Valor mínimo a cobrar (segmentos de serviço)

`apps/custo/valor_minimo.py` calcula, a partir do custo operacional ativo da unidade, o **piso** a cobrar por período (o usuário soma a margem de lucro por cima; por isso não há margem no cálculo). Só vale quando `Cliente.segmento.tipo_precificacao` é `servico` ou `ambos`.

| Período | Fórmula (centavos) |
|---|---|
| mensal | custo operacional mensal |
| quinzenal | mensal ÷ 2 |
| semanal | mensal × 7 ÷ `dias_mes` |
| diário | mensal ÷ `dias_mes` |
| hora | mensal ÷ `horas_mes` |

`horas_mes` (padrão 220) e `dias_mes` (padrão 30) vêm de `ConfiguracaoMaoDeObra` da unidade e as contas reaproveitam `maodeobra.calculo.derivados_do_mes`. Arredondamento meio-para-cima uma vez por valor. `incluir_mao_de_obra=True` soma a folha mensal dos colaboradores ativos (opcional; o painel usa só o custo operacional). Unidade sem custo: `sem_custo=True` e valores zero.

Onde aparece: bloco "Valor mínimo a cobrar" do painel (permissão `custo.view_custooperacional`, módulo `custo-operacional`) e `GET /api/v1/painel/` (`blocos.valor_minimo`, valores em centavos em `centavos`). Sem tabelas/colunas novas. Testes: `apps/custo/tests_valor_minimo.py`.
