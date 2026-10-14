# apps/insumo — Insumos

## Objetivo

Cadastro dos insumos usados nas receitas/produtos de **um cliente**, com cálculo automático do **preço por KG/L/UN** a partir da quantidade comprada e do valor pago. Dinheiro é guardado em **centavos** (inteiro).

Exemplo: 100 g por R$ 4,00 → `quantidade=100`, `medida=Gramas`, `preco_pago=400`, `preco_unidade=4000` (R$ 40,00/KG).

## Estrutura

| Arquivo | Papel |
|---|---|
| `models.py` | `Insumo` e `calcular_preco_unidade()` (usa `web_project/regras/conversao.py`). |
| `forms.py` | Formulário com máscara de dinheiro e validações. |
| `views.py` | Lista (pesquisa, ordenação, paginação, exportação), criar, editar e excluir (lógico). |
| `exports.py` | Excel (openpyxl) e PDF (reportlab). |
| `urls.py` | `insumo`, `insumo/novo`, `insumo/<id>/editar`, `insumo/<id>/excluir` — todas com `module_required("insumo", ...)`. |
| `templates/` | `insumo.html` (lista + tour), `insumo_form.html`, `_th_ordenavel.html`. |
| `tests.py`, `tests_migracao.py` | Regras de preço, isolamento entre clientes, permissões, migração. |

## Relacionamentos e isolamento

```text
Usuário → Cliente → Segmento → Módulo "insumo" habilitado → permissão insumo.* → Insumo (cliente = o do usuário)
```

- `ClienteScopedMixin` (`web_project/mixins.py`) filtra **sempre** por `request.user.profile.cliente` e força o cliente ao salvar: um `pk` de outro cliente na URL resulta em **404**; o `cliente` nunca vem do POST. A API (`/api/v1/insumos/`) usa a mesma regra.
- Matriz e filiais **não compartilham** insumos: cada cliente enxerga só os seus.
- Exclusão é **lógica** (inativa) e auditada.

## Fluxo

```text
URL /insumo → module_required → InsumoListView (ClienteScopedMixin) → Insumo.objects.filter(cliente) → insumo.html
POST /insumo/novo → InsumoCreateView → form → Insumo.save() (recalcula preco_unidade) → auditoria → redirect
```

## Banco de dados — `insumo_insumo`

**Fase 3** — colunas adicionadas (migrations `0008` estrutura, `0009` dados, `0010` unicidade; separadas para SQLite e PostgreSQL):

| Coluna | Tipo | Nulo | Padrão | Regra |
|---|---|---|---|---|
| `uuid` | uuid | não | `uuid4()` | Identificador público estável, único, não editável (QR Code das etiquetas). Insumos existentes receberam um UUID aleatório na migração 0009 |
| `dias_validade` | smallint | sim | NULL | Validade padrão em dias (>= 1), usada pela etiqueta com validade "calculada" |

**Histórico do insumo:** `insumo/<id>/historico` (permissão `insumo.view_insumo`, mesmo tenant) lista, a partir da trilha de auditoria, as alterações desse insumo (preço, quantidade, unidade, mínimo...), com filtro por campo e período, evolução com variação percentual e linha do tempo. Não há tabela própria; registros de exportação não aparecem.

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `cliente_id` | FK | sim | Dono do insumo | `cliente_cliente` (PROTECT) |
| `nome` | varchar(150) | sim | — | **unique** com `cliente` |
| `marca` | varchar(60) | não (padrão `''`) | Marca do produto comprado; letras, números, espaço e `. , & ' + / ( ) % -`; vazio é válido (migration `0007`, reversível) | — |
| `categoria_id` | FK | não | Legado, oculto (não editável) | `categoria_categoria` (PROTECT) |
| `medida_id` | FK | sim | Unidade de medida | `medida_medida` (PROTECT) |
| `quantidade` | decimal(15,4) | sim | Quantidade comprada (> 0) | — |
| `preco_pago` | bigint | sim | Valor pago em **centavos** | — |
| `preco_unidade` | bigint | não | Preço por KG/L/UN em centavos (calculado, não editável) | — |
| `ativo` | bool | sim | — | — |
| `criado_em` / `atualizado_em` | datetime | sim | Controle | — |

Índices: único (`cliente`, `nome`); (`cliente`, `ativo`).

## Marca, listagem e filtros

- **Marca do produto** é opcional no cadastro, na edição e na API (`marca`). É **auditada**: na criação entra no registro de auditoria quando preenchida (`AUDITORIA_NA_CRIACAO`, `apps/auditoria/utils.py::registrar_criacao`) e na edição como antes/depois (como qualquer campo). O mecanismo atualizado é a tabela `auditoria_registroauditoria` (única do projeto).
- **Listagem**: colunas Insumo (com a marca discreta), **Preço** (por KG/L/UN), Unidade (só para matriz com filiais), Status, Cadastrado em e Ações — as colunas Quantidade e Valor pago saíram da tabela (continuam no cadastro, na exportação e na API).
- **Filtros** (um formulário só, responsivo): pesquisa (nome, marca ou unidade; com ícone), **status** (ativos por padrão / inativos / todos), unidade e itens por página. Filtros, ordenação e paginação andam juntos, sempre sobre o escopo de unidades do usuário.
- **Dropdowns de insumos** (processados e fichas técnicas): o insumo aparece como "Açúcar (Kg)" e a unidade da linha mostra só a **maior grandeza** (Kg, L, Un, M); ao escolher o insumo a unidade já é preenchida (`src/assets/js/unidade-do-insumo.js`). O servidor continua aceitando qualquer unidade da mesma dimensão (itens antigos em gramas, API e conversões intactos).

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| fórmula do preço por unidade | `web_project/regras/conversao.py` |
| nova coluna | `models.py` → migration → `forms.py` → `insumo.html` → `exports.py` → `api/serializers.py` |
| ações do tour | bloco `page_js` de `insumo.html` (ver `apps/tour/README.md`) |
