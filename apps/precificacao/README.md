# apps/precificacao — Precificação

Calcula o **preço de venda** de um produto a partir do custo da ficha técnica, do custo operacional, das taxas do canal de venda e da formação do lucro. Um produto pode ter **várias precificações** (canais e margens diferentes).

```text
Produto ← Ficha técnica (custo_total) ─┐
Canal de venda (taxas) ────────────────┼→ Precificação → preço de venda, lucro, CMV
Custo operacional + método do lucro ───┘
```

## Fórmulas e ordem das operações (`calculos.py`, funções puras)

Valores em centavos (int); percentuais em pontos percentuais (10 = 10 %).

1. **Custo total antes do canal e do lucro**: `C = custo da ficha + custo operacional`.
2. **Canal**: `t = taxas percentuais ÷ 100` (incidem sobre o **preço de venda**) e `F = taxas fixas em reais` (por venda).
3. **Preço de venda `P`**, conforme o método — as taxas do canal e o lucro saem do **mesmo** preço, sem circularidade:

| Método | Fórmula | Base do percentual |
|---|---|---|
| **Valor em reais** (`L`) | `P = (C + L + F) ÷ (1 − t)` | — |
| **Markup** `k %` | `P = (C × (1 + k/100) + F) ÷ (1 − t)` | o **custo** (pode passar de 100 %) |
| **Margem** `m %` | `P = (C + F) ÷ (1 − t − m/100)` | o **preço de venda** (nunca chega a 100 %) |

   Margem e markup **nunca se misturam**: são fórmulas distintas. Exemplo sem canal, custo R$ 10,00 e 50 %: margem → **R$ 20,00**; markup → **R$ 15,00**.
4. `P` é arredondado **para cima** ao centavo (`ROUND_CEILING`): arredondar para baixo comeria a margem pedida.
5. **Taxas percentuais em reais** = `P × t` (ROUND_HALF_UP); **lucro = P − C − F − taxas percentuais**. Assim custo + taxas + lucro somam exatamente `P`. Com margem `m`, o lucro é `m %` de `P` (±1 centavo de arredondamento).
6. **CMV**: custo da mercadoria vendida por unidade = **custo da ficha** (os insumos; o custo operacional não é mercadoria). `CMV % = custo da ficha ÷ P × 100`. É o conceito de `indicadores.cmv` (estoque inicial + compras − estoque final) aplicado a uma unidade vendida.

**Casos de borda** (testados): sem taxas (`t = 0`, `F = 0`) a conta segue igual; `P = 0` (custo e lucro zerados) dá CMV 0 % sem divisão por zero; margem + taxas ≥ 100 % → erro claro ("deve ser menor que 100%"); taxas ≥ 100 %, valores negativos, floats e valores acima do teto também são recusados. A decomposição fecha o preço exatamente em todos os métodos.

Exemplo do canal de referência (taxas 26,20 % + 1,59 % = 27,79 %), custo R$ 12,00 e margem 20 %: `P = 12,00 ÷ (1 − 0,2779 − 0,20) = R$ 22,99`, taxas R$ 6,39, lucro R$ 4,60.

## Canal de venda

Duas formas (campos `canal`, `canal_nome`, `canal_descricao`, `taxa_percentual`, `taxa_fixa`):

- **Canal cadastrado** (`canal` preenchido): nome, descrição e taxas são **cópia** do canal e acompanham as mudanças dele — `apps/canal/services.py::salvar_canal` chama `recalcular_do_canal`; se as novas taxas inviabilizarem alguma precificação (margem + taxas ≥ 100 %), a edição do canal é **recusada e desfeita**.
- **Personalizado** (`canal` vazio): nome, descrição, taxa em % e/ou em reais digitados na própria precificação.

## Recálculo automático

`apps/ficha/services.py::recalcular_ficha` chama `services.recalcular_da_ficha` sempre que o custo da ficha muda (ou a ficha é salva/trocou de produto), dentro da mesma transação. Logo, alterar um insumo, um processado (qualquer nível), uma ficha ou um canal reprecifica tudo, até o preço de venda. `manage.py recalcular_custos` refaz tudo (idempotente). Valores derivados recalculados pelo sistema não geram linha de auditoria; a gravação/edição feita pelo usuário gera.

## Custo operacional (automático, com ajuste manual)

O campo "Custo operacional da categoria (R$)" e o resumo "Custo operacional por categoria" vêm da **distribuição automática** do módulo Custo operacional (`custo.services.custo_operacional_da_categoria`: valor da categoria do produto ÷ quantidade vendida). Em branco, o servidor usa esse valor.

- `custo_operacional_manual = False` (padrão): a precificação **acompanha a distribuição** — quando custos, vendas da categoria ou ajustes de categoria mudam, ela é refeita (sinais `post_save`/`post_delete` em `apps/precificacao/apps.py` → `services.recalcular_custo_operacional_automatico`, só da unidade afetada; falha de uma linha nunca barra a gravação do custo).
- `custo_operacional_manual = True`: o usuário digitou um valor **diferente** do automático; ele é **preservado** (nunca sobrescrito) e a tela mostra "Ajustado manualmente". Digitar o valor automático ou "Restaurar automático" volta ao modo automático.
- A tela preenche o campo sozinha e mostra o total da categoria no mês e se ele foi ajustado no Custo operacional; o cálculo do preço continua só no servidor.
- A migration `0005` marcou como manuais as precificações antigas com custo operacional > 0 (o valor foi digitado pelo usuário).

## Telas (`module_required("precificacao", ...)`)

| Rota | Permissão | O que faz |
|---|---|---|
| `precificacao` | `view_precificacao` | **Tabela** (sem a coluna "Ficha técnica": o vínculo continua no banco) ou **cartões** (um por lançamento), escolhidos em `?visao=tabela\|cartoes` e lembrados no navegador (`precificacao-lista.js`); mesmos filtros, paginação e estados vazios nas duas. Lista paginada: pesquisa, produto, canal, método do lucro, status, unidade, ordenação (nome, produto, canal, preço, lucro, CMV, data), exportação **Excel/PDF** (mesmo queryset filtrado/escopado; auditada). |
| `precificacao/novo` | `add_precificacao` | Formulário com **Salvar** e **Salvar e adicionar outra**. |
| `precificacao/<id>/editar` | `change_precificacao` | Edição (o dono nunca muda). |
| `precificacao/<id>/excluir` (POST) | `delete_precificacao` | Exclusão (precificação não tem dependentes). |
| `precificacao/calcular` (POST) | `view_precificacao` | Prévia: valida o formulário e devolve itens da ficha e toda a decomposição. |

**Formulário**: ficha em lista suspensa com busca (select2), canal cadastrado ou personalizado, custo operacional, método do lucro (campos mudam conforme o método), **itens da ficha com custo e total no topo e no rodapé** e um resumo: canal de venda, custo operacional por categoria, custo da ficha, custo total antes do canal e do lucro, taxas do canal, método e valor do lucro, lucro em reais, preço de venda e CMV. A prévia vem **sempre do servidor** (`src/assets/js/precificacao-form.js` só exibe a resposta de `calcular_preco`); estados: "Calculando...", erro de fórmula em destaque e "Prévia indisponível" se a rede falhar.

## Banco de dados — `precificacao_precificacao`

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente_id` | FK PROTECT | Unidade dona (escopo matriz/filial, ver `apps/acl/README.md`) |
| `nome` | varchar(150) | **Identificação gerada** (`Ficha — Canal`, ou só a ficha); não é digitada nem única — a precificação é da ficha técnica |
| `ficha_id` | FK PROTECT | Ficha técnica usada |
| `produto_id` | FK PROTECT | Produto da ficha (copiado ao salvar/recalcular) |
| `canal_id` | FK PROTECT, nulo | Canal cadastrado |
| `canal_nome`, `canal_descricao` | varchar | Nome/descrição do canal (cópia ou digitado) |
| `taxa_percentual` | decimal(7,4) | Taxas em % (cópia do canal ou digitada) |
| `taxa_fixa` | bigint (centavos) | Taxas em reais |
| `custo_operacional` | bigint (centavos) | Informado |
| `metodo_lucro` | varchar(10) | `margem`, `markup` ou `valor` |
| `percentual_lucro` | decimal(9,4) | Margem ou markup (%) |
| `valor_lucro` | bigint (centavos) | Lucro em reais (método `valor`) |
| `custo_ficha`, `custo_total`, `taxas_percentuais_valor`, `lucro`, `preco_venda` | bigint (centavos) | **Calculados** (`lucro` pode ser negativo só por arredondamento/dados incoerentes) |
| `cmv_percentual` | decimal(9,2) | **Calculado** |
| `custo_operacional_manual` | bool | **False** = acompanha a distribuição do Custo operacional; **True** = ajustado pelo usuário (migration `0004`/`0005`) |
| `ativo`, `criado_em`, `atualizado_em` | — | Controle |

Índice `(cliente, ativo)`. Migration `0003_precificacao_sem_nome_digitado`: remove a unicidade `(cliente, nome)` e preenche a identificação das linhas existentes (reversível). Migration `0002_precificacoes`: se existirem precificações antigas (sem cliente/ficha/produto) ela **para com mensagem** em vez de inventar dados.

Testes: `apps/precificacao/tests.py` (fórmulas e bordas, recálculo em cadeia insumo → processado → ficha → precificação, canal, telas, validações, filtros, exportação, prévia e isolamento de tenant, matriz/filial).

## Mão de obra na tela de precificação

A tabela "Itens da ficha técnica" lista, além dos insumos, a **mão de obra** opcional da ficha (segmentos de serviço) com subtotais: *subtotal dos insumos*, *subtotal da mão de obra* e *custo total da ficha*. Antes só os insumos apareciam, embora o custo total (`Ficha.custo_total`, calculado no servidor em `apps/ficha/services.py::recalcular_ficha`) já somasse a mão de obra — a conta na tela não fechava. A correção é só de exibição: `views.py::resumo_da_ficha` monta linhas e subtotais (usado na tela e na prévia `precificacao/calcular`, chaves `mao_de_obra`, `subtotal_insumos`, `subtotal_mao_de_obra`; `itens[].quantidade_texto` novo). Nada é somado no navegador e a mão de obra não é contada duas vezes (o total vem da ficha). Sem mão de obra a tela fica como antes. Sem alteração de banco.
