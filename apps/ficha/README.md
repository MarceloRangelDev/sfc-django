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

### Receita e observações (Fase 1)

Colunas **adicionadas** em `ficha_ficha` pela migration `ficha.0003_receita_e_observacoes` (aditiva e reversível; linhas existentes ficam com texto vazio, nada é recalculado):

| Coluna | Tipo | Nulo | Padrão | Regra |
|---|---|---|---|---|
| `receita` | text (limite de **5000** caracteres, validado no servidor) | não | `''` | Modo de preparo. Texto simples: quebras de linha e listas com `- ` são mantidas; **HTML nunca é interpretado** (telas e PDF escapam). |
| `observacoes` | text (limite de **1000**) | não | `''` | Observações. |

- **Permissão `ficha.ver_receita`** (criada pela migration; concedida a *Administrador* e *Gestor*; para os demais, pela tela de Papéis/permissão especial por usuário). Sem ela, os campos **não existem** no formulário nem na API (um POST forjado não grava e o texto existente é preservado) e a pergunta do PDF não aparece. Na API, tentar gravar sem a permissão devolve **403**.
- **Tenant:** os campos pertencem ao registro, que já é filtrado pelo cliente/unidades do usuário.
- **Auditoria:** registra *que* receita/observações mudaram (`(conteúdo omitido)`), nunca o texto (`AUDITORIA_SEM_CONTEUDO` no model).
- **Exportação individual (PDF/Excel):** `ficha/<id>/exportar?formato=pdf|xlsx&incluir_receita=1` (tela) e `GET /api/v1/fichas-tecnicas/{id}/exportar/` (API). Na lista, o botão *Exportar* abre a pergunta **"Incluir receita e observações?"** quando o registro tem esses textos e o usuário tem a permissão. A escolha é respeitada; sem a permissão o pedido é ignorado. Cada exportação é auditada. Código comum em `web_project/documento.py`.

## Sem nome digitado

A ficha técnica fica vinculada ao **Produto**: o campo "Nome da ficha técnica" saiu do cadastro/edição. A coluna `nome` continua (única por unidade, para não quebrar fichas antigas, precificações e relatórios) e agora é **derivada** a cada gravação por `services.nome_da_ficha`: o nome do produto ("Pizza"); se o produto já tem outra ficha na unidade, ganha sufixo ("Pizza (2)"). Nenhum dado existente foi alterado e não há migration; fichas antigas mantêm o nome que tinham até serem salvas de novo.

## Isolamento

Escopo de unidades (`ClienteScopedMixin`): própria unidade e, para a matriz com `cliente.<ação>_itensfilial`, as filiais; `pk` fora do escopo → 404. Criação é na unidade do usuário (insumos e produtos selecionáveis são os dela).

Testes: `apps/ficha/tests.py` (custos, cascata insumo → processado → ficha, unidade incompatível, recálculo geral idempotente, outro tenant intocado, telas, validações, filtros, exclusão, permissões, matriz/filial, exportação, prévia e seu isolamento).

## Serviços: insumos opcionais e mão de obra na ficha

Nos segmentos **Serviço** e **Serviço e Produto** (os mesmos que usam o módulo Mão de obra) os **insumos deixam de ser obrigatórios** na ficha e ela pode ter **mão de obra cadastrada** (colaboradores com as horas de cada um por execução): só insumos, só mão de obra, os dois ou nenhum (custo 0). Nos segmentos de **Produto** nada mudou (ao menos um insumo, sem mão de obra).

- Custo da mão de obra: `valor_hora do colaborador × horas`, arredondado meio-para-cima (`services.custo_da_mao_de_obra`); o custo total da ficha soma insumos + mão de obra e o navegador só exibe o que `ficha/calcular` devolve (aceita `mao_de_obra` além de `itens`).
- Colaborador da **mesma unidade** e ativo (o já usado segue válido se desativado); sem repetir colaborador; horas > 0. Colaborador usado em ficha **não é apagado** (só desativado).
- Mudar o valor da hora de um colaborador refaz as fichas e precificações que o usam (sinal em `apps.py` → `recalcular_fichas_do_colaborador`); se alguma precificação ficar inviável o cadastro do colaborador **não é bloqueado** (aviso no log) e `recalcular_custos` refaz tudo.
- API: `mao_de_obra` na entrada/saída de `/api/v1/fichas-tecnicas/` (`itens` agora opcional nesses segmentos; PATCH sem o campo mantém).

Tabela **`ficha_fichamaodeobra`** (migração `ficha.0004_mao_de_obra_na_ficha`, aditiva): `id`; `ficha_id` FK CASCADE → `ficha_ficha`; `colaborador_id` FK **PROTECT** → `maodeobra_colaborador`; `horas` decimal(7,2) > 0; `custo` bigint (centavos, calculado); restrição única `ficha_mao_de_obra_unica (ficha, colaborador)`. Sem impacto nos dados existentes.

## Metro em 1, 2 e 3 dimensões

Insumo, item de processado e item de ficha aceitam a unidade **Metro** como linear, área (m²) ou volume (m³): `dimensoes`, `comprimento`, `largura`, `altura` (colunas aditivas; `quantidade` vira o produto das medidas e segue sendo o valor das contas). Insumo e item precisam ter o mesmo número de dimensões. Regra, decisões, colunas e migrações: **apps/medida/README.md** (seção "Metro em 1, 2 e 3 dimensões"); código em `web_project/dimensoes.py` e `web_project/regras/conversao.py`.
