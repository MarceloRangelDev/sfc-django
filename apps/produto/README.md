# apps/produto — Produtos

Cadastro dos produtos vendidos por uma unidade (cliente matriz/filial), cada um em uma **categoria**. O custo vem das **Fichas técnicas** (`apps/ficha`) e o preço de venda das **Precificações**; aqui ficam só os dados cadastrais.

```text
Usuário → Cliente → Segmento → Módulo "produto" habilitado → permissão produto.<ação>_produto → Produto (escopo de unidades)
URL → module_required → View (ClienteScopedMixin / ListaPorUnidadeMixin / ListaPaginadaMixin) → produto.html
```

## Telas (`urls.py`, todas com `module_required("produto", ...)`)

| Rota | Permissão | O que faz |
|---|---|---|
| `produto` | `view_produto` | Lista paginada. Filtros: pesquisa (nome, descrição, categoria), categoria, status (padrão: ativos), unidade (matriz/filial). Ordenação por nome, categoria e data (lista fechada). Exportação **Excel/PDF** (`?export=xlsx|pdf`). |
| `produto/novo` | `add_produto` | Cadastro com **Salvar** e **Salvar e adicionar outro**. |
| `produto/<id>/editar` | `change_produto` | Edição (o dono nunca muda). |
| `produto/<id>/excluir` (POST) | `delete_produto` | Sem fichas/precificações → apagado; **em uso → só desativado**, com aviso (`web_project/mixins.py::ExcluirOuDesativarView`). |

- **Exportação**: usa exatamente o queryset filtrado da tela (mesmo escopo de unidades, pesquisa, categoria, status e ordenação), exige `view_produto`, é auditada (`acao=exportar`) e neutraliza fórmulas no Excel (`web_project/exportacao.py`).
- **Categoria**: só categorias **ativas da mesma unidade** são aceitas (o campo rejeita ids de fora); ao editar, a categoria atual continua válida mesmo se desativada depois. Desativar uma categoria **não** altera os produtos dela.
- Criação é sempre na unidade do usuário; a lista mostra filiais (selo Matriz/Filial + filtro) para a matriz com `cliente.view_itensfilial`; editar/excluir filial exige `change_/delete_itensfilial` (ver `apps/acl/README.md`).

## Banco de dados — `produto_produto`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `cliente_id` | FK | sim | Unidade dona | `cliente_cliente` (PROTECT) |
| `nome` | varchar(150) | sim | **unique** com `cliente` | — |
| `descricao` | varchar(255) | não | — | — |
| `categoria_id` | FK | sim | — | `categoria_categoria` (PROTECT) |
| `ativo` | boolean | sim | Desativado some dos seletores novos | — |
| `criado_em` / `atualizado_em` | datetime | sim | Controle | — |

Índice `(cliente, ativo)`. Migration `0002_cadastro_de_produtos`: a tabela antiga era só âncora de permissão; se existirem linhas antigas (sem cliente/categoria) a migration **para com mensagem** em vez de inventar dados.

## Manutenção

Novo campo → model + migration + form + template (+ coluna em `exportar`). Testes: `apps/produto/tests.py` (filtros, ordenação, paginação, categoria inválida, exclusão segura, permissões, tenant, matriz/filial, exportação com filtro/escopo/fórmula).
