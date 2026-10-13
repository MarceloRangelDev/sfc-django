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
