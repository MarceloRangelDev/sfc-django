# apps/processado — Processados e Receitas

Cadastro de **receitas / insumos processados** de uma unidade (Cliente matriz ou filial). A ficha técnica soma o custo dos insumos usados; o resultado vira um **Insumo gerado**, que aparece na lista geral de Insumos e pode ser usado por outros processados (vários níveis).

```text
Usuário → Cliente → Segmento → Módulo "processado" habilitado → permissão processado.<ação>_processado → tela
```

## Exemplo (camarão)

1 kg de camarão custa R$ 39,90 (`preco_pago=3990`, 1 kg). A receita "Camarão limpo" usa 1 kg e rende 500 g:

| Resultado | Cálculo |
|---|---|
| Custo do item | 3990 × 1000 g ÷ 1000 g = **3990** centavos |
| Custo total | soma dos itens = **R$ 39,90** |
| Insumo gerado | quantidade 500 g, valor pago R$ 39,90 → **R$ 79,80 por kg** (regra normal de Insumo) |
| Fator de rendimento | 500 g ÷ 1000 g = **0,5** (redução de 50 %) |

## Fórmulas (`recalculo.py`)

- **Custo do item** = `preco_pago` do insumo × quantidade usada (em unidade base) ÷ quantidade comprada (em unidade base). Conversão pela tabela `medidas` (`fator_base`); só arredonda no final (ROUND_HALF_UP, centavos). Dimensões diferentes (massa × volume) são **recusadas** — não há conversão implícita.
- **Custo total** = soma dos custos dos itens (cada um já arredondado, para a soma bater com o que a tela mostra).
- **Custo unitário** = custo total ÷ rendimento, na unidade padrão da dimensão (KG ou L) — `preco_por_unidade_padrao`.
- **Fator de rendimento** = rendimento ÷ soma das quantidades usadas (unidade base). Só existe se **todos** os itens estão na mesma dimensão da saída; senão fica vazio. Nunca divide por zero (quantidades > 0 são validadas e o caso zero devolve vazio/erro claro).
- **Unidade de saída**: somente massa ou volume (g, kg, mL, L) — `DIMENSOES_DE_SAIDA`.

## Insumo gerado

`Processado.insumo` (OneToOne, `related_name="processado_origem"`) aponta para um `insumo.Insumo` criado/atualizado a cada gravação (`nome`, `ativo` e a **maior grandeza** da saída: `quantidade` = 1, `medida` = 1 kg / 1 L / 1 un / 1 m e `preco_pago` = custo dessa unidade; ex.: 500 g a R$ 39,90 → 1 kg a R$ 79,80). Exceção: se o insumo já tem saldo em estoque em outra unidade, mantém a unidade do saldo com a quantidade equivalente a 1 unidade de referência. A saída aceita massa, volume, unidade e comprimento (`DIMENSOES_DE_SAIDA`). A lista de Insumos não mostra "valor pago" para insumos gerados. Depois do `migrate`, insumos gerados no formato antigo são convertidos automaticamente (`normalizar_insumos_gerados`; também pelo comando `recalcular_custos`). Por isso:

- ele **não pode ser editado nem excluído** pela tela/API de Insumos (aparece com a etiqueta "Processado"); altere o processado;
- o nome do processado não pode repetir o de um insumo da mesma unidade;
- excluir o processado (lógico) inativa o insumo gerado.

## Formulário (tela)

- **Ficha técnica com custo em tempo real**: cada linha mostra o custo e o total aparece no topo e no rodapé (`.total-ficha`), calculados pelo servidor em `processado/calcular` (`ProcessadoCalcularView`, que reutiliza `FichaCalcularView`: mesma conta de `custo_do_item`, só insumos do escopo do usuário). O navegador não calcula nada.
- **Valor por extenso**: sob "Quantidade de rendimento" aparece o texto ("100 gramas") vindo de `medidas/calcular` (singular/plural pela tabela `medidas`).
- **Salvar e adicionar outro**: grava e volta ao formulário em branco (só ao criar); com erro, nada é gravado.
- **Unidades**: os dropdowns (rendimento e itens) listam todas as unidades ativas com o nome completo, "Quilo (kg)"; o valor enviado continua sendo o id. Em cada linha da ficha a primeira opção ("Unidade...") vem selecionada: **quem escolhe a unidade é o usuário** (escolher o insumo não muda a unidade; sem unidade o custo mostra "Escolha a unidade"). O servidor converte a quantidade para a unidade do insumo: custo = preço do insumo × quantidade na unidade escolhida (Abacaxi a R$ 5,00/un, 2 un = R$ 10,00).
- **Quantidade por extenso em cada linha**: sob o campo da quantidade aparece "2 unidades", "100 gramas" assim que há quantidade e unidade (vem do servidor junto com o custo, `extenso` na resposta de `processado/calcular` e `ficha/calcular`).

## Recálculo em cascata

Alterar custo/unidade de um insumo recalcula, na **mesma transação**, tudo o que depende dele:

1. `Insumo` é gravado por qualquer caminho (tela, API, admin) → sinal `post_save` (`signals.py`, ligado em `apps.py`) chama `propagar_insumos`.
2. `_coletar_dependentes`: níveis sucessivos de processados **ativos do mesmo cliente** que usam os insumos alterados e, depois, os insumos gerados por eles.
3. `_ordenar_por_dependencia` (ordem topológica): quem é usado é calculado antes de quem usa; **cada processado é recalculado uma única vez** (dependência em losango incluída).
4. `recalcular_processado`: atualiza `ProcessadoItem.custo`, `custo_total`, `fator_rendimento` e o insumo gerado (gravado com `_sem_propagacao`, pois o motor já cuida dos próximos níveis).
5. `GANCHOS_POS_RECALCULO`: funções `f(insumo_ids, cliente_id)` rodam no fim — é onde **Ficha técnica e Precificação** devem se conectar quando existirem.

Garantias:

- **Tenant**: todas as consultas filtram pelo `cliente_id` do insumo; um processado só usa insumos da própria unidade (o campo do formulário rejeita outros).
- **Ciclo**: `verificar_ciclo` impede *salvar* um processado que passe a depender de si ("dependência circular"); o motor também detecta ciclo vindo de dados corrompidos (`CicloDeDependenciaError`) sem recursão infinita.
- **Atomicidade**: qualquer falha desfaz o recálculo inteiro (e a gravação do processado). O erro é registrado em log (apenas ids, sem dados pessoais).
- **Nova tentativa**: `python manage.py recalcular_custos [--cliente ID]` reprocessa tudo, em ordem, sem duplicar nada (idempotente).
- **Exclusões**: insumo usado por processado ativo **não pode ser excluído** (mensagem com os nomes), nem trocar de dimensão (kg → L). Processado cujo insumo gerado é usado por outro não pode ser excluído/inativado. Nada é apagado em cascata: a exclusão é lógica.

## Tabelas e colunas

`processado_processado`: `id`, `cliente_id` (FK PROTECT), `nome`, `rendimento_quantidade` decimal(15,4), `rendimento_medida_id` (FK `medidas`, PROTECT), `custo_total` bigint (centavos, calculado), `fator_rendimento` decimal(12,4) nulo (calculado), `insumo_id` (OneToOne `insumo_insumo`, PROTECT, nulo), `ativo`, `criado_em`, `atualizado_em`. Único `(cliente, nome)`; índice `(cliente, ativo)`.

`processado_processadoitem`: `id`, `processado_id` (FK CASCADE — o item é parte da receita), `insumo_id` (FK PROTECT), `medida_id` (FK PROTECT), `quantidade` decimal(15,4), `custo` bigint (centavos, calculado). Único `(processado, insumo)`.

Migration `0002_receitas_e_processados`: a tabela antiga era só âncora de permissão. Se existirem linhas antigas (sem cliente/unidade de saída) a migration **para com mensagem** em vez de inventar dados.

### Receita e observações (Fase 1)

Colunas **adicionadas** em `processado_processado` pela migration `processado.0003_receita_e_observacoes` (aditiva e reversível; linhas existentes ficam com texto vazio, nada é recalculado):

| Coluna | Tipo | Nulo | Padrão | Regra |
|---|---|---|---|---|
| `receita` | text (limite de **5000** caracteres, validado no servidor) | não | `''` | Modo de preparo. Texto simples: quebras de linha e listas com `- ` são mantidas; **HTML nunca é interpretado** (telas e PDF escapam). |
| `observacoes` | text (limite de **1000**) | não | `''` | Observações. |

- **Permissão `processado.ver_receita`** (criada pela migration; concedida a *Administrador* e *Gestor*; para os demais, pela tela de Papéis/permissão especial por usuário). Sem ela, os campos **não existem** no formulário nem na API (um POST forjado não grava e o texto existente é preservado) e a pergunta do PDF não aparece. Na API, tentar gravar sem a permissão devolve **403**.
- **Tenant:** os campos pertencem ao registro, que já é filtrado pelo cliente/unidades do usuário.
- **Auditoria:** registra *que* receita/observações mudaram (`(conteúdo omitido)`), nunca o texto (`AUDITORIA_SEM_CONTEUDO` no model).
- **Exportação individual (PDF/Excel):** `processado/<id>/exportar?formato=pdf|xlsx&incluir_receita=1` (tela) e `GET /api/v1/processados/{id}/exportar/` (API). Na lista, o botão *Exportar* abre a pergunta **"Incluir receita e observações?"** quando o registro tem esses textos e o usuário tem a permissão. A escolha é respeitada; sem a permissão o pedido é ignorado. Cada exportação é auditada. Código comum em `web_project/documento.py`.

## Telas e permissões

Rotas (`urls.py`, todas com `module_required("processado", ...)`): `processado` (lista paginada, pesquisa, status, ordenação, filtro de unidade matriz/filial), `processado/novo`, `processado/<id>/editar`, `processado/<id>/excluir` (POST). Escopo de unidades e permissão `cliente.<ação>_itensfilial`: ver `apps/acl/README.md`. Criação é sempre na unidade do usuário. Custos mostrados na edição são os da última gravação (a tela não calcula nada: o servidor é a única fonte).

## Arquivos

| Arquivo | Papel |
|---|---|
| `models.py` | `Processado`, `ProcessadoItem` |
| `recalculo.py` | cálculo de custo/fator, ciclo e motor de propagação |
| `services.py` | `salvar_processado` / `excluir_processado` (transação + auditoria), `mensagem_insumo_em_uso` |
| `signals.py`, `apps.py` | gatilho do recálculo ao gravar um Insumo |
| `forms.py`, `views.py`, `templates/` | formulário + formset da ficha, lista e telas |
| `management/commands/recalcular_custos.py` | nova tentativa idempotente |

Testes: `apps/processado/tests.py` (exemplo do camarão, cascata, losango, ciclo, falha/idempotência, tenant, bloqueios, telas).
