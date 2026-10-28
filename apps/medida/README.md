# apps/medida — Unidades de medida

## Objetivo

Cadastro **global** (não é por cliente) das unidades de medida (g, kg, ml, L, un...) e dos fatores de conversão usados no cálculo do preço por KG/L/UN dos insumos. É um módulo **essencial** de Configurações: só depende da permissão `medida.*`, concedida por padrão ao Administrador.

## Estrutura

`models.py` (`Medida`), `forms.py`, `views.py` (lista, criar, editar, ativar/inativar, excluir, prévia de cálculo `medidas/calcular`), `urls.py` (`module_required("medida", ...)`), `templates/`, `tests.py`.

## Fluxo

```text
URL /medidas → module_required("medida", "medida.view_medida") → MedidaListView → Medida → medida.html
Insumo.save() → conversao.preco_por_unidade_padrao(medida.fator_base, ...) → preco_unidade
```

## Banco de dados — `medida_medida`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `medida` | varchar(60) | sim | Nome | **unique** |
| `sigla` | varchar(15) | sim | — | **unique** |
| `descricao` | varchar(255) | não | — | — |
| `equivale` | varchar | não | Texto "equivale a" | — |
| `dimensao` | varchar(15) | sim | massa, volume ou unidade | — |
| `fator_base` | decimal | sim | Fator para a unidade padrão (KG/L/UN) | — |
| `nome_singular` / `nome_plural` | varchar(60) | não | Para o "por extenso" | — |
| `ativo` | bool | sim | — | — |
| `usuario_id` | FK | não | Quem cadastrou | `auth_user` (SET NULL) |
| `criado_em` / `atualizado_em` | datetime | sim | Controle | — |

## Manutenção

Mudar conversões → `web_project/regras/conversao.py`; nova dimensão → `models.py::DIMENSOES` + migration.

## Metro em 1, 2 e 3 dimensões (insumo, processado e ficha)

Só a unidade **Metro** (comprimento, fator 1; `medida_e_metro`) aceita dimensões. O usuário escolhe **linear** (comprimento), **área** (comprimento × largura, m²) ou **volume** (× altura, m³). A regra mora em `web_project/regras/conversao.py` (`quantidade_por_dimensoes`) e os formulários/serializers a reutilizam (`web_project/dimensoes.py`: `DimensoesMixin` nas colunas, `DimensoesFormMixin` nos formulários); o JS (`src/assets/js/dimensoes.js`) só mostra/esconde campos e **nada é calculado no navegador**.

- **Decisão central:** `quantidade` continua sendo o valor das contas. Com 2 ou 3 dimensões ela é **derivada** (produto das medidas em metros, 4 casas, `ROUND_HALF_UP` só no final) e gravada junto de `comprimento`/`largura`/`altura`; por isso custo, preço por unidade, estoque e PDV não mudaram.
- **Validação:** medidas obrigatórias conforme as dimensões, maiores que zero, produto ≥ 0,0001 (ex.: 0,001 × 0,001 é recusado). Fora do Metro o campo é ignorado e grava `dimensoes = 1`.
- **Compatibilidade:** insumo e item precisam ter o **mesmo número de dimensões** (`custo_do_item` recusa m² contra m linear); insumo já usado em receita **não muda de dimensões**. Registros antigos ficam em 1 dimensão com as medidas nulas. Processado em Metro linear só compara rendimento com itens lineares (`fator_de_rendimento` devolve `None` com itens em m²/m³). O rendimento do processado continua linear.
- **Rótulos:** preço por `M`, `M²` ou `M³`; `descricao_dimensoes`/`quantidade_texto` mostram "2,4 × 1,2 m = 2,88 m²".
- **Prévias:** `/medidas/calcular` (insumo) e `ficha/calcular` / `processado/calcular` (linhas) aceitam `dimensoes`, `comprimento`, `largura`, `altura` e devolvem o que o servidor calcularia.
- **API:** `dimensoes`, `comprimento`, `largura`, `altura` (e `descricao_dimensoes`) em `/api/v1/insumos/` e nos `itens` de `/api/v1/processados/` e `/api/v1/fichas-tecnicas/`; `/api/v1/medidas/` traz `aceita_dimensoes`. Contrato aditivo: sem os campos vale 1 dimensão como antes.

Colunas novas (migrações aditivas e reversíveis: `insumo.0013`, `processado.0004`, `ficha.0005`, todas `dimensoes_do_metro`) em **`insumo_insumo`**, **`processado_processadoitem`** e **`ficha_fichaitem`**:

| Coluna | Tipo | Nulo | Padrão | Finalidade |
|---|---|---|---|---|
| `dimensoes` | smallint positivo | não | `1` (`db_default`) | 1 = linear, 2 = área, 3 = volume |
| `comprimento` | decimal(15,4) | sim | — | Metros (só com 2 ou 3 dimensões) |
| `largura` | decimal(15,4) | sim | — | Metros (2 e 3 dimensões) |
| `altura` | decimal(15,4) | sim | — | Metros (3 dimensões) |

Sem novas tabelas, índices ou restrições. Testes: `apps/api/tests_dimensoes.py`.
