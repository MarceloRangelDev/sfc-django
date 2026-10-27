# apps/precificacaoservico — Precificação, proposta e acompanhamento de serviços

## Objetivo e limites

Ajuda profissionais e empresas de **serviços** (pintor, mecânico, técnico, advogado, contador...) a **calcular, apresentar e acompanhar preços**: custo-hora a partir da remuneração desejada, despesas e provisões; propostas por hora, dia, semana, quinzena, mês, unidade/etapa ou pacote; formação do preço (margem sobre o preço ou markup sobre o custo); aceite; e comparação **previsto x realizado**.

- **Só para segmentos Serviço e Serviço e Produto.** Produto puro não recebe o módulo (`seed_modulos.SOMENTE_SERVICO`, migração `modulo.0026`) e o servidor recusa o acesso mesmo que alguém habilite o módulo por engano (403 nas telas e na API). Nada do segmento de produtos foi alterado: `precificacao` (produtos), `orcamento`, `custo` e `ficha` continuam como estavam.
- **Não é consultoria tributária.** Impostos e taxas são **percentuais estimados informados pelo usuário** (ou pelo contador); a tela avisa isso.
- Fora do escopo desta versão: emissão fiscal, integração contábil, agenda avançada, gestão de equipe, atualização automática de tributos, envio automático da proposta por e-mail/WhatsApp (há impressão/PDF pelo navegador) e aceite pelo cliente por link público (o aceite é registrado pelo usuário).

## Como usar (menu *Precificação de serviços*)

1. **Custo-hora e premissas** (`/precificacao-servico/configuracao`): moeda, remuneração mensal desejada, dias de trabalho, horas por dia, horas não faturáveis (ou horas faturáveis informadas), custo variável por hora, impostos/taxas estimados e margem desejada. A tela mostra o **custo-hora calculado** e sua composição (a remuneração aparece em linha própria).
2. **Despesas e provisões** (`/precificacao-servico/despesas`): aluguel, internet, contabilidade, seguros, depreciação etc., ou provisões mensais (férias, 13º, ferramentas), cada uma com valor e periodicidade (semanal, mensal, bimestral, trimestral, semestral, anual), convertidos para base mensal.
3. **Modelos de serviço** (`/precificacao-servico/modelos`): serviços que se repetem (forma de cobrança, unidade, preço-base, horas por unidade, **margem própria**). O ícone de nova proposta copia os valores do modelo; mudar o modelo depois não altera propostas.
4. **Propostas** (`/precificacao-servico`): cabeçalho do cliente, itens de trabalho, custos diretos, formação do preço, escopo, prazo, validade e condições. O painel *Resumo calculado* mostra, em tempo real (calculado pelo servidor), custo, preço mínimo, preço-alvo, total, impostos e taxas, lucro, margem, markup resultante e alertas. No detalhe: enviar, aprovar (com aceite), recusar, cancelar, concluir, **atualizar premissas** (ação explícita), imprimir/salvar PDF e, após aprovar, registrar **lançamentos reais** (horas, custos, deslocamentos, extras), **pagamentos** e ver o **previsto x realizado**.

## Conceitos (não se confundem)

| Conceito | Significado |
|---|---|
| **Custo** | O que o serviço consome: horas × custo-hora (que já inclui a **remuneração** do profissional) + custos diretos. |
| **Remuneração** | Quanto o profissional quer ganhar pelo trabalho. Entra no custo; **não é lucro**. |
| **Preço** | Valor cobrado do cliente. |
| **Impostos e taxas** | Percentuais **sobre o preço** (estimativas informadas pelo usuário; inclui o custo financeiro do parcelamento da proposta). |
| **Lucro** | `preço − custo − impostos e taxas`. O faturamento total nunca é tratado como lucro. |
| **Margem** | Lucro como **% do preço de venda**. |
| **Markup** | Acréscimo como **% do custo**. Markup de 50% dá margem de 33,33%; margem de 50% equivale a markup de 100%. |

## Cálculos (`calculos.py`, funções puras com `Decimal` e centavos)

```text
Horas disponíveis  = dias de trabalho no mês × horas por dia
Horas faturáveis   = disponíveis − não faturáveis        (ou o valor informado, que não pode passar das disponíveis)
Custo fixo mensal  = remuneração + despesas fixas mensais + provisões mensais
Custo fixo/hora    = custo fixo mensal ÷ horas faturáveis
Custo-hora total   = custo fixo/hora + custo variável por hora
```

Horas faturáveis ≤ 0 (ou acima de 744 h) **bloqueiam** o cálculo. Despesas em outra periodicidade viram base mensal (semanal = × 52 ÷ 12).

**Horas de cada item** = quantidade × horas por unidade. Se o item não informa, vale o padrão da forma de cobrança: hora = 1 h; dia = jornada (horas por dia); semana = 5 dias; quinzena = 10 dias; mês = horas faturáveis do mês; unidade/pacote = 0 (o esforço deve ser informado). Itens por hora aceitam **arredondamento em blocos** (15/30/60 min, sempre para cima) e **horas mínimas**. O **tempo de deslocamento** sempre entra no custo; se “cobrar deslocamento” estiver marcado, entra também na base do preço.

**Custos diretos** (quantidade × custo unitário) têm três tratamentos:

- **Incluído no preço**: entra no custo e na base do preço (recebe margem).
- **Absorvido**: entra no custo, mas **não aumenta o preço** (reduz o lucro).
- **Discriminado ao cliente**: cobrado à parte pelo valor do custo, acrescido só de impostos/taxas (`custo ÷ (1 − impostos − taxas)`), sem margem.

**Preço** (percentuais sobre o preço; soma obrigatoriamente < 100%, senão o cálculo é bloqueado e o motivo explicado):

```text
Preço-alvo (margem) = base ÷ (1 − impostos − taxas − margem) + repasse discriminado
Preço-alvo (markup) = base × (1 + markup)                    + repasse discriminado
Preço mínimo        = (custo total − discriminado) ÷ (1 − impostos − taxas) + repasse discriminado   (lucro zero)
```

Arredondamento de preço **para cima** ao centavo (para baixo comeria a margem pedida). Exemplos conferidos nos testes: custo R$ 500, 10% de impostos/taxas e 20% de margem → **R$ 714,29** (impostos R$ 71,43; lucro R$ 142,86); 5 h × R$ 60 + R$ 180 + R$ 40 → custo R$ 520 → **R$ 742,86**.

**Preço praticado**, em ordem: *preço fechado* informado → soma `quantidade × preço unitário` dos itens (quando **todos** os itens têm preço) → preço-alvo. Depois: **cobrança mínima**; **acréscimo** percentual (urgência, fim de semana, risco) e em valor (deslocamento, hora extra); **desconto** em valor ou %. O resultado é recalculado a cada passo: impostos/taxas = total × percentuais; lucro; margem; markup resultante. **Alertas**: abaixo do custo, prejuízo após impostos/taxas, abaixo do preço mínimo, abaixo da margem desejada e “desconto levou a margem para negativa”.

## Propostas antigas não mudam (snapshot)

Ao criar a proposta, o sistema grava em `premissas` (JSON) o **retrato** do custo-hora, das horas, dos percentuais e da **versão da configuração**, e em `resultado` o cálculo completo. Editar a proposta reusa esse retrato; mudar configuração, despesas ou modelos **não** altera propostas existentes. Só a ação explícita **“Atualizar para as premissas de hoje”** (tela) / `atualizar_premissas: true` (API), em proposta ainda editável (rascunho/enviada), refaz o retrato. Lançamentos de horas sem custo informado usam o custo-hora **do retrato da proposta**. Os dados reais **não** alteram preços futuros.

## Situações

`rascunho → enviada → aprovada → concluída`; `rascunho/enviada → recusada`; `rascunho/enviada/aprovada → cancelada`. Só rascunho/enviada é editável; só **rascunho** pode ser excluído (as demais se cancelam, preservando o histórico). Lançamentos e pagamentos exigem proposta **aprovada ou concluída**. Aprovar registra data e nome do aceite.

## Previsto x realizado (`services.relatorio`)

```text
Variação de custo = custo real − custo previsto
Resultado         = valor recebido − custos reais − impostos e taxas registrados (taxas dos pagamentos)
```

Também mostra horas previstas/reais, extras aprovados, valor recebido, pendente e a receber.

## Permissões e acesso

Cadeia: Usuário → Cliente → Segmento (Serviço / Serviço e Produto) → módulo `precificacao-servico` habilitado → permissão. Como o módulo revela remuneração, custos e margem, as permissões ficam em `acl/padroes.SOMENTE_AUTORIZADOS`: **Administrador** (ver/criar/alterar/excluir) e **Gestor** (ver/criar/alterar; excluir é do Administrador). Operador e Visualizador não recebem acesso (podem ser concedidos manualmente na tela de Papéis). Modelos de permissão: `configuracaoservico`, `despesafixa`, `modeloservico`, `proposta`, `propostaitem`, `propostacusto`, `lancamento`, `pagamento`.

Isolamento: toda consulta filtra pela unidade do usuário (`clientes_dos_itens`: própria unidade e, para a matriz com a permissão de itens das filiais, as filiais). Id de outra unidade = **404**; cliente final de outra unidade é recusado; unidade, número, situação, totais e premissas **nunca** vêm do corpo da requisição. A configuração e a criação usam sempre a unidade do próprio usuário.

## API (Swagger em `/api/docs/`, tag *precificacao-servico*)

Prefixo `/api/v1/precificacao-servico/`. Dinheiro em **centavos**; percentuais em pontos percentuais.

| Método e rota | Função | Permissão |
|---|---|---|
| `GET/PATCH configuracao/` | Consulta e altera as premissas | `view/change_configuracaoservico` |
| `GET custo-hora/` | Custo-hora e composição de hoje | `view_configuracaoservico` |
| `POST simular/` | Simula preço (não grava), mesmo corpo da proposta | `view_proposta` |
| `GET/POST despesas/`, `GET/PUT/PATCH/DELETE despesas/{id}/` | Despesas e provisões (paginado, `q`, `ativo`, ordenação) | `*_despesafixa` |
| `GET/POST modelos/`, `GET/PUT/PATCH/DELETE modelos/{id}/` | Modelos de serviço (com uso: só desativa) | `*_modeloservico` |
| `GET/POST propostas/`, `GET/PATCH/PUT/DELETE propostas/{id}/` | Propostas (filtros `q`, `situacao`, `ordenar`; DELETE só rascunho) | `*_proposta` |
| `POST propostas/{id}/status/` | enviada, aprovada (com `aceite_nome`), recusada, cancelada, concluida | `change_proposta` |
| `GET/POST propostas/{id}/lancamentos/`, `DELETE .../lancamentos/{lid}` | Execução real | `view/change_proposta` |
| `GET/POST propostas/{id}/pagamentos/`, `DELETE .../pagamentos/{pid}` | Pagamentos | `view/change_proposta` |
| `PATCH propostas/{id}/datas/` | Início e conclusão | `change_proposta` |
| `GET propostas/{id}/relatorio/` | Previsto x realizado | `view_proposta` |

Erros no envelope padrão da API: 400 (campo inválido), 403 (sem permissão, módulo ou segmento), 404 (inexistente ou de outra unidade), 422 (regra de negócio: percentuais ≥ 100%, horas faturáveis zeradas, situação inválida, desconto maior que a proposta...). Exemplo:

```bash
curl -X POST /api/v1/precificacao-servico/propostas/ -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{
  "destinatario_nome": "Maria", "descricao": "Instalação elétrica", "modo": "hora",
  "itens": [{"descricao": "Mão de obra", "modo": "hora", "quantidade": "5"}],
  "custos": [{"descricao": "Peças", "categoria": "material", "quantidade": "1", "custo_unitario": 18000, "repasse": "incluido"}]}'
```

## Telas e tours

Rotas web: `ps-proposta-list|create|edit|detalhe|imprimir|calcular|acao`, `ps-config`, `ps-despesa-*`, `ps-modelo-*`. A impressão (`/propostas/{id}/imprimir`) é uma página legível para imprimir ou **salvar como PDF pelo navegador**: não mostra custos internos, lucro nem margem; custos “discriminados” aparecem só se a proposta marcar “mostrar custos discriminados”. Tours (`src/assets/js/tours-modulos.js`): `ps-propostas`, `ps-proposta-form`, `ps-proposta-detalhe`, `ps-config`, `ps-despesas`, `ps-despesa-form`, `ps-modelos`, `ps-modelo-form` (registrados também em `apps/tour/tests_modulos.py`). A prévia do formulário usa `POST propostas/calcular` (JavaScript só exibe o que o servidor devolve: `src/assets/js/ps-proposta-form.js`).

## Migrações

- `precificacaoservico.0001_initial`: cria as tabelas abaixo e concede as permissões do módulo aos perfis Administrador e Gestor **já existentes** (só acrescenta). Reversível (apaga as tabelas novas; as permissões ficam).
- `modulo.0026_modulo_precificacao_servico`: acrescenta o módulo ao catálogo, aos segmentos Serviço e Serviço e Produto e aos clientes desses segmentos (não toca módulos nem habilitações existentes). Num banco novo, retira o módulo do segmento de produto puro (a migração 0003 liga todo o catálogo a todos os segmentos).

Nenhuma tabela existente foi alterada.

## Tabelas

Todas têm `cliente_id` (FK `cliente_cliente`, a unidade/tenant). Dinheiro em centavos (bigint). Datas de criação/alteração são automáticas.

**`precificacaoservico_configuracaoservico`** — uma linha por unidade (`cliente` OneToOne, CASCADE, índice único). Colunas: `moeda` varchar(3) `BRL`; `remuneracao_mensal` bigint ≥ 0 (0); `dias_mes` decimal(4,1) (22); `horas_dia` decimal(4,2) (8); `horas_nao_faturaveis` decimal(6,2) (0); `horas_faturaveis_informadas` decimal(6,2) **nulo**; `custo_variavel_hora` bigint (0); `impostos_pct`, `taxas_pct` decimal(5,2) (0); `margem_pct` decimal(5,2) (20); `versao` int (1; sobe a cada alteração); `atualizado_em`.

**`precificacaoservico_despesafixa`** — despesas e provisões. `cliente` (PROTECT); `natureza` (`despesa`/`provisao`); `descricao` varchar(150); `categoria` varchar(15) (`outras`); `valor` bigint; `periodicidade` (`mensal`); `vigencia_inicio` date nulo; `ativo` bool (true); `criado_em`, `atualizado_em`. Índice `psdespesa_cliente_ativo_idx (cliente, ativo)`.

**`precificacaoservico_modeloservico`** — `cliente` (PROTECT); `nome` varchar(150); `categoria` varchar(80); `modo`; `unidade` varchar(20); `preco_base` bigint nulo; `horas_por_unidade` decimal(7,2) nulo; `margem_pct` decimal(5,2) nulo; `descricao` text(1000); `ativo`; timestamps. Único `psmodelo_nome_unico_por_unidade (cliente, nome)`.

**`precificacaoservico_proposta`** — `cliente` (PROTECT); `numero` int (sequencial por unidade); `modelo` FK nula (SET NULL); `consumidor` FK nula (SET NULL; contatos são **copiados** para a proposta); `destinatario_nome/email/whatsapp`; `descricao` varchar(200); `modo`; `status` (`rascunho`); `validade` date nula; `prazo`; `condicoes_pagamento`; textos de escopo (`escopo_incluido`, `escopo_excluido`, `etapas`, `materiais_incluidos`, `materiais_excluidos`, `limites`, `regras_extras`, `observacoes`); parâmetros do preço (`metodo_preco` `margem`, `margem_pct` e `markup_pct` nulos, `preco_fechado` nulo, `minimo_cobranca` 0, `horas_minimas` 0, `arredondamento_minutos` 0, `horas_deslocamento` 0, `cobra_deslocamento` false, `desconto_tipo` `valor`, `desconto` decimal(12,2) 0 — centavos ou %, `acrescimo_pct` 0, `acrescimo_valor` 0, `custo_financeiro_pct` 0, `detalhar_custos` false); `premissas` JSON e `resultado` JSON (retrato e cálculo); `custo_total`, `total` bigint, `lucro` bigint **com sinal**; `aceite_em`, `aceite_nome`; `data_inicio`, `data_conclusao`; `criado_por` (SET NULL); timestamps. Único `psproposta_numero_unico_por_unidade (cliente, numero)`; índice `psproposta_cliente_status_idx (cliente, status)`.

**`precificacaoservico_propostaitem`** — itens de trabalho: `proposta` (CASCADE); `descricao`; `modo`; `quantidade` decimal(10,3) > 0; `unidade`; `horas_por_unidade` decimal(7,2) nulo; `preco_unitario` bigint nulo; `ordem`. Sem `cliente` próprio: o tenant é o da proposta (toda consulta passa pela proposta já filtrada).

**`precificacaoservico_propostacusto`** — custos diretos: `proposta` (CASCADE); `categoria` (material, terceiro, deslocamento, alimentacao, equipamento, taxa, reserva, outro); `descricao`; `quantidade`; `unidade`; `custo_unitario` bigint; `repasse` (`discriminado`/`incluido`/`absorvido`); `ordem`.

**`precificacaoservico_lancamento`** — execução real: `proposta` (CASCADE); `tipo` (horas, custo, deslocamento, extra); `data`; `descricao`; `horas` decimal(7,2) (0); `custo` bigint (0); `receita` bigint (0; só extra); `criado_por`; `criado_em`.

**`precificacaoservico_pagamento`** — `proposta` (CASCADE); `valor` bigint; `data`; `meio`; `taxa` bigint (0, restrição `pspagamento_taxa_ate_valor`: taxa ≤ valor); `status` (`recebido`/`pendente`); `observacao`; `criado_por`; `criado_em`.

Itens, custos, lançamentos e pagamentos são apagados junto com a proposta (CASCADE), que só pode ser excluída enquanto rascunho. Todas as operações de criação/alteração/exclusão são **auditadas** (módulo `precificacaoservico`).

## Partes mais complexas

- `calculos.calcular_proposta`: concentra a regra de repasse (incluído/absorvido/discriminado), preço praticado, mínimos, acréscimos, descontos e alertas. Valida tudo no servidor (percentuais, float recusado, centavos inteiros, limites).
- `services.salvar_proposta`: transação única (proposta + itens + custos + auditoria) com nova tentativa quando duas gravações disputam o mesmo número; decide entre reaproveitar o retrato e refazê-lo.
- `services.registrar_lancamento`: horas sem custo valem horas × custo-hora do retrato.

## Como configurar, manter e evoluir

1. Cadastre/ajuste a **configuração** e as **despesas**; confira o custo-hora.
2. Garanta o módulo no cliente (*Clientes → Módulos*) — fica automático nos segmentos de serviço — e as permissões no perfil (Papéis).
3. Para nova forma de cobrança: incluir em `calculos.MODOS`/`models.MODO_CHOICES` e definir `horas_padrao_do_modo`. Para nova periodicidade: `calculos.FATOR_MENSAL` e `DespesaFixa.PERIODICIDADE_CHOICES`.
4. Suporte a outras moedas exigiria conversão e formatação por moeda (hoje só BRL).

## Premissas adotadas (sem resposta às perguntas iniciais)

App novo restrito a serviço; proposta própria (o `Orcamento` de produtos/serviços não foi alterado); custo-hora independente de `maodeobra`; pagamentos registrados só na proposta (sem gerar contas a receber); despesas próprias do módulo (o rateio do `custo` operacional de produtos não foi tocado); moeda BRL única; aceite registrado manualmente; um tour por tela; impressão/PDF pelo navegador.
