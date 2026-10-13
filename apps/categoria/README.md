# apps/categoria — Categorias

## Objetivo

Categorias criadas por cada unidade (cliente matriz/filial) para organizar seus produtos, com os dados de venda de cada uma (quantidade vendida, faturamento) e os indicadores derivados: **ticket médio** e **% do faturamento total**.

## Estrutura

`models.py` (`Categoria`), `calculos.py` (ticket médio e percentual), `forms.py`, `views.py`, `urls.py`, `templates/categoria.html` (lista + tour) e `categoria_form.html`, `tests.py`.

## Isolamento e fluxo

```text
Usuário → Cliente → Segmento → Módulo "categoria" habilitado → permissão categoria.* → Categoria (escopo de unidades)
URL → module_required → View (ClienteScopedMixin / ListaPorUnidadeMixin) → Categoria → categoria.html
```

Filtro pelo escopo do usuário no backend (a própria unidade e, para a matriz com `cliente.<ação>_itensfilial`, as filiais — ver `apps/acl/README.md`); `pk` fora do escopo → 404; cliente definido pelo servidor. API: `/api/v1/categorias/` (continua só com `id`, `nome` e datas).

## Ações (cada uma exige a permissão correspondente, checada no servidor)

| Ação | URL | Permissão | Regra |
|---|---|---|---|
| Ativar/Desativar | `categoria/<id>/alternar-ativo` (POST) | `change_categoria` | Só liga/desliga `ativo`. **Sem cascata**: produtos, fichas e precificações que já usam a categoria permanecem intactos; ela apenas deixa de aparecer em formulários novos. |
| Excluir | `categoria/<id>/excluir` (POST) | `delete_categoria` | Sem nenhum uso → apagada de verdade. **Em uso** (qualquer FK de outra tabela, via `Categoria.em_uso()`, que enxerga produtos/fichas futuros sozinha) → só **desativada**, com aviso. Nunca apaga histórico. |
| Prévia de ticket/% | `categoria/calcular` (GET) | `view_categoria` | Devolve JSON usando as mesmas funções da gravação; o navegador não calcula nada. |

Toda ação é auditada (`registrar_edicao` / `registrar_exclusao`). O formulário de criação tem **Salvar** e **Salvar e adicionar outra**.

## Indicadores (`calculos.py`)

- **Ticket médio** = faturamento ÷ quantidade vendida, em centavos (ROUND_HALF_UP). Sem vendas → R$ 0,00.
- **% do faturamento** = faturamento da categoria ÷ faturamento total × 100, 2 casas. Total zero → 0,00 %.
- O **total** da lista é o das categorias do escopo **com os mesmos filtros** (busca, status, unidade), em todas as páginas — por isso os percentuais fecham 100 % do que está filtrado. Na tela de edição a prévia usa o total do escopo (sem a própria categoria) + o valor digitado.
- Não existe ainda módulo de vendas: quantidade e faturamento são **informados** na categoria. Quando houver vendas reais, basta alimentar `quantidade_vendida`/`faturamento` a partir delas (ou trocar por agregação) sem mudar a tela.

## Banco de dados — `categoria_categoria`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `nome` | varchar(150) | sim | — | **unique** com `cliente` |
| `cliente_id` | FK | sim | Dono da categoria | `cliente_cliente` (PROTECT) |
| `ativo` | boolean | sim (padrão 1) | Desativada some dos formulários novos | — |
| `quantidade_vendida` | inteiro ≥ 0 | sim (padrão 0) | Informada pelo usuário | — |
| `faturamento` | bigint (**centavos**) | sim (padrão 0) | Informado pelo usuário | — |
| `criado_em` / `atualizado_em` | datetime | sim | Controle | — |

Migration `0003_ativo_e_dados_de_venda`: só acrescenta colunas com valor padrão no banco; categorias existentes ficam ativas e com vendas zeradas.

## Manutenção

Nova regra de acesso → `urls.py`; novo campo → model + migration + form + template (+ `api/serializers.py` se for para a API).
