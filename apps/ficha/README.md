# apps/ficha — Fichas técnicas

Ficha técnica de um produto: os **insumos** (inclusive os gerados por Processados) e as quantidades usadas, com o custo de cada item e o **custo total**. A Precificação (próxima fase) parte do `custo_total` da ficha.

```text
Produto ← Ficha → FichaItem → Insumo ← (insumo gerado por) Processado → ProcessadoItem → Insumo ...
```

Um produto pode ter **mais de uma ficha** (variações), cada uma com nome único na unidade.

## Cálculo (igual ao dos processados)

`services.recalcular_ficha` usa `apps/processado/recalculo.py::custo_do_item`: **custo do item = preço pago × quantidade usada ÷ quantidade comprada**, convertendo na unidade base da dimensão (250 g de farinha comprada a R$ 5,00/kg = R$ 1,25) e arredondando só no final. Massa × volume não se convertem (erro claro). **Custo total = soma dos custos dos itens**.

## Recálculo automático

`apps.py` registra `recalcular_fichas_dos_insumos` em `GANCHOS_POS_RECALCULO` (ver `apps/processado/README.md`). Resultado: alterar preço/quantidade/unidade de um insumo — ou de um processado, em qualquer nível — recalcula, **na mesma transação**, as fichas do mesmo cliente que o usam (inclusive inativas). `manage.py recalcular_custos` refaz todas (idempotente). Falha → tudo desfeito.

Proteções ligadas ao insumo (`processado.services.mensagem_insumo_em_uso`): insumo usado em ficha **não pode ser excluído** nem **trocar de dimensão** (kg → L), na tela e na API.

## Telas (`module_required("ficha", ...)`)

| Rota | Permissão | O que faz |
|---|---|---|
| `ficha` | `view_ficha` | Lista paginada: pesquisa (ficha/produto), produto, status, unidade, ordenação (nome, produto, custo, data), exportação **Excel/PDF** (mesmo queryset filtrado/escopado; auditada). |
| `ficha/novo` | `add_ficha` | Formulário com **Salvar** e **Salvar e adicionar outra**. |
| `ficha/<id>/editar` | `change_ficha` | Edição; os itens são substituídos de forma atômica. |
| `ficha/<id>/excluir` (POST) | `delete_ficha` | Sem uso → apagada (itens junto); em uso por precificações → só desativada. |
| `ficha/calcular` (POST JSON) | `view_ficha` | Prévia de custos do formulário. |

**Formulário**: insumo em lista suspensa **com busca** (select2), unidade de medida, quantidade e **custo calculado por linha**; o **custo total aparece no topo e no rodapé** e os dois são atualizados juntos (`.total-ficha`). A prévia é **sempre do servidor**: `src/assets/js/ficha-form.js` envia as linhas a `ficha/calcular` (que usa `custo_do_item`, restrito aos insumos do escopo do usuário) e só exibe a resposta; erros de unidade aparecem na célula. Ao salvar, o servidor recalcula tudo de novo.

Validações: ao menos 1 item, quantidade > 0, um insumo só uma vez, insumos/produto só da unidade da ficha (ids de fora são rejeitados pelo campo), produto ativo.

## Banco de dados

`ficha_ficha`: `id`, `cliente_id` (FK PROTECT), `produto_id` (FK PROTECT), `nome` varchar(150) (**derivado do produto**, ver abaixo), `custo_total` bigint (**centavos**, calculado), `ativo`, `criado_em`, `atualizado_em`. Único `(cliente, nome)`; índice `(cliente, ativo)`.

`ficha_fichaitem`: `id`, `ficha_id` (FK CASCADE — o item pertence à ficha), `insumo_id` (FK PROTECT), `medida_id` (FK PROTECT), `quantidade` decimal(15,4), `custo` bigint (centavos, calculado). Único `(ficha, insumo)`.

Migration `0002_fichas_tecnicas`: se existirem fichas antigas (sem cliente/produto) a migration **para com mensagem** em vez de inventar dados.

## Sem nome digitado

A ficha técnica fica vinculada ao **Produto**: o campo "Nome da ficha técnica" saiu do cadastro/edição. A coluna `nome` continua (única por unidade, para não quebrar fichas antigas, precificações e relatórios) e agora é **derivada** a cada gravação por `services.nome_da_ficha`: o nome do produto ("Pizza"); se o produto já tem outra ficha na unidade, ganha sufixo ("Pizza (2)"). Nenhum dado existente foi alterado e não há migration; fichas antigas mantêm o nome que tinham até serem salvas de novo.

## Isolamento

Escopo de unidades (`ClienteScopedMixin`): própria unidade e, para a matriz com `cliente.<ação>_itensfilial`, as filiais; `pk` fora do escopo → 404. Criação é na unidade do usuário (insumos e produtos selecionáveis são os dela).

Testes: `apps/ficha/tests.py` (custos, cascata insumo → processado → ficha, unidade incompatível, recálculo geral idempotente, outro tenant intocado, telas, validações, filtros, exclusão, permissões, matriz/filial, exportação, prévia e seu isolamento).
