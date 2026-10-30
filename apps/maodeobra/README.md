# Mão de obra por hora e por dia (`apps/maodeobra`)

Colaboradores (valor da hora e, opcionalmente, da diária) e **apontamentos** de trabalho por hora (início, fim, pausa) ou por dia, calculados **no servidor** (tela e API usam o mesmo cálculo, `calculo.py`, funções puras com `Decimal` e centavos inteiros).

## Quem usa

Somente empresas de segmento **Serviço** ou **Serviço e Produto** (decisão do cliente). Segmento de produto puro não recebe o módulo (`seed_modulos.SOMENTE_SERVICO`, migração `modulo.0016`), e o servidor recusa gravar apontamento fora dessa regra.

## Regras de cálculo (configuráveis por unidade em *Mão de obra → Regras*)

| Regra | Padrão | Observação |
|---|---|---|
| Arredondamento | por **minuto** (sem arredondar) | opções: para cima a cada 15 min ou à hora cheia |
| Jornada normal | 8 h/dia | o que passa disso é hora extra |
| Adicional de hora extra | **50 %** | sobre o valor da hora |
| Adicional de domingo | 0 % (desligado) | quando > 0, vale para todas as horas do domingo |

- Horas = (fim − início) − pausas; fim menor que início passa da meia-noite (máx. 24 h).
- Valor da hora × tempo normal + valor da hora × (1 + extra%) × tempo extra; cada valor arredondado **uma vez** ao centavo.
- Por dia: `dias × valor_dia` (vazio = valor da hora × jornada). Sem hora extra.
- Exemplo (hora R$ 20,00, 8 h, 50 %): 9 h 30 min → R$ 160,00 + R$ 45,00 = **R$ 205,00**.
- Cada apontamento guarda um **snapshot** dos parâmetros e do resultado: mudar valor ou regras depois não altera lançamentos antigos.

## Telas e API

`/mao-de-obra` (apontamentos, filtros), `/mao-de-obra/relatorio` (por colaborador e período), `/mao-de-obra/colaboradores`, `/mao-de-obra/regras`.
API v1: `/mao-de-obra/colaboradores/`, `/mao-de-obra/apontamentos/` (CRUD paginado), `/mao-de-obra/configuracao/` (GET/PATCH), `/mao-de-obra/relatorio/`.

## Segurança e auditoria

Tudo é da unidade do usuário (outra unidade devolve 404); colaborador e apontamento de outra unidade não são alcançados. Todo CRUD é auditado (valores monetários nos campos `AUDITORIA_MONETARIOS`). Colaborador com apontamentos é desativado, não excluído.

## Tabelas

`maodeobra_colaborador`, `maodeobra_apontamento`, `maodeobra_configuracaomaodeobra` (migração `maodeobra.0001`, aditiva).

## Limites

O custo calculado **não** alimenta automaticamente a Precificação de serviços (o preço do serviço continua sendo definido pela Precificação existente); o relatório serve de base para esse custo.

## Serviços (cadastro) e mão de obra na ficha

- **Serviços** (aba *Serviços*, `/mao-de-obra/servicos`): lista/cadastra/altera/exclui os serviços usados no apontamento. Serviço é um produto do tipo `servico` (ver `apps/produto/README.md`); não há cadastro duplicado. O apontamento só oferece serviços **ativos da unidade** (o atual segue válido na edição). Exclusão: apagar se sem uso; **só desativar** se há apontamento, ficha ou precificação. Permissões: módulo `mao-de-obra` + `produto.view/add/change/delete_produto`; só segmentos Serviço e Serviço e Produto.
- **Fichas de serviço** podem incluir colaboradores e horas (ver `apps/ficha/README.md`). Alterar o valor da hora recalcula as fichas/precificações que usam o colaborador.


---

# Funções de mão de obra, custo completo, simulação de promoção e dashboard

> Acrescentado em 2026-10-30. **Informativo**: nada desta seção altera o valor da hora/dia dos colaboradores, os apontamentos, as fichas nem as precificações. O que já existia (apontamentos, `h/mês`, valor por mês → hora/dia/semana/quinzena) segue exatamente como estava.

## Finalidade e limites

Cadastrar **funções** (cargos) com a composição de custo de quem as exerce — salário base, adicionais, DSR, provisões, encargos, benefícios e custos indiretos —, **vincular** a função ao colaborador (com histórico e itens personalizáveis), **calcular o custo completo** por dia, semana, quinzena, mês, bimestre, trimestre, semestre e ano, **simular promoções** (planejamento) e acompanhar tudo num **dashboard**.

Limites assumidos:

* **Nenhum percentual ou obrigação legal vem pronto.** O sistema não sabe qual o regime, a convenção coletiva ou a localidade da empresa; todo item (INSS patronal, FGTS, 13º, férias + 1/3, DSR, adicional noturno etc.) é cadastrado pelo usuário, com o campo *Condição / elegibilidade / fonte* para registrar a premissa. A CBO é digitada (6 dígitos + descrição); não há catálogo externo.
* O custo completo **não** alimenta o valor da hora usado em apontamentos e fichas (decisão do cliente). Hora/dia continuam vindo de `Colaborador.valor_mes` ÷ `horas_mes`/`dias_mes` (`calculo.derivados_do_mes`).
* Só segmentos **Serviço** e **Serviço e Produto**, dentro do módulo `mao-de-obra` (nenhum módulo novo no catálogo). Dados sensíveis: **Administrador** (tudo) e **Gestor** (ver/criar/alterar; excluir é do Administrador). Operador e Visualizador não acessam.

## Fluxos

1. **Funções** (`/mao-de-obra/funcoes`): lista, cadastro/edição, desativação. Função com colaborador vinculado (ou histórico) nunca é apagada, só desativada.
2. **Itens de custo** (na tela da função): cada item tem categoria, tipo (valor fixo ou percentual sobre uma base), periodicidade (mensal ou anual ÷ 12), vigência (início/fim), condição/fonte e a marca **personalizável**. Para mudar um valor, **encerre o item e crie outro** com nova vigência: o passado continua calculável. Excluir um item que já vigorou **encerra** (fim = ontem); só item futuro e sem personalização é apagado.
3. **Vínculo com o colaborador** (edição do colaborador → *Função cadastrada* + *Vigente a partir de*):
   * traz o salário da função para `valor_mes` (e, portanto, hora/dia/h/mês, pelas regras de sempre); se o item de salário é **personalizável** e o usuário informa `valor_mes`, vale o valor dele; senão vale o da função;
   * trocar de função **encerra o vínculo anterior na véspera** e abre outro (`VinculoFuncao`); a nova data deve ser posterior ao início da função atual; personalizações **não** passam para a função nova;
   * o texto livre `Colaborador.funcao` continua existindo (compatibilidade); ao escolher uma função cadastrada ele é preenchido com o nome dela. Colaborador sem função segue funcionando (a composição fica marcada como **incompleta**: só vale o salário do cadastro).
4. **Composição do colaborador** (`/mao-de-obra/colaboradores/<id>/composicao`): itens com *valor da função* × *valor deste colaborador* (herdado / personalizado / do cadastro), custo por período e hora, histórico de funções. Só itens marcados como personalizáveis têm campo; campo vazio volta a herdar. O salário é personalizado no próprio cadastro do colaborador.
5. **Simulação** (`/mao-de-obra/simulacoes`): por **pessoa** (colaborador + função de destino opcional) ou por **função** (função de origem + destino opcional = todos os colaboradores ativos dela). Parâmetros de salário: *novo salário* **ou** *ajuste %* sobre o atual (nunca os dois); sem nenhum, vale o salário da função de destino. Persiste **só os parâmetros** (`SimulacaoPromocao`); o resultado é recalculado a cada consulta. **Nunca altera** colaborador, função ou vínculo; não existe "aplicar" nesta versão.
6. **Dashboard** (`/mao-de-obra/dashboard`): custo total e médio no período escolhido, nº de colaboradores, distribuição por função, composição por categoria, custo em todos os períodos, comparação com outra data e últimos cenários. Filtros: data, comparar com, função, período exibido e unidade. Mostra o aviso **Dados incompletos** quando há colaborador sem função/salário.

## Arquitetura (onde está cada responsabilidade)

| Arquivo | Responsabilidade |
|---|---|
| `custos.py` | **Fórmulas** (funções puras, `Decimal`/centavos): ordem das categorias, bases permitidas, validação de item (`validar_item`), `calcular`, `periodos_do_mes`, `variacao`. Única fonte das regras de cálculo. |
| `models_funcao.py` | Modelos `FuncaoMaoDeObra`, `ComponenteFuncao`, `VinculoFuncao`, `PersonalizacaoColaborador`, `SimulacaoPromocao` (importados por `models.py`). |
| `composicao.py` | Camada de dados: monta os itens do colaborador numa data (`composicoes_em_lote`, 4 consultas, sem N+1), `trocar_funcao`, `simular`, `indicadores` (dashboard), `encerrar_ou_excluir_componente`, `como_dict` (JSON). Telas e API usam as **mesmas** funções. |
| `forms.py` / `forms_funcao.py` | Validação de entrada (tela **e** API usam os mesmos formulários). `ColaboradorForm` ganhou `funcao_vinculada`/`funcao_inicio`. |
| `views_funcao.py`, `urls.py`, `templates/` | Telas. |
| `apps/api/maodeobra_funcoes.py`, `apps/api/maodeobra.py` | API v1 e documentação OpenAPI. |

## Fórmulas, bases, períodos e arredondamento

**Ordem de cálculo** (cada categoria só usa como base o que vem antes, então não há circularidade nem item contado duas vezes):

`salário → adicional → DSR → provisão → encargo → benefício → indireto`

* Item **fixo**: valor em centavos; periodicidade *anual* é dividida por 12.
* Item **percentual**: `percentual × base`, com o percentual como **taxa mensal** sobre a base mensal.
* **Bases**: `salario`; `salario_adicionais` (salário + adicionais); `remuneracao` (salário + adicionais + DSR); `remuneracao_provisoes` (remuneração + provisões).

| Categoria | Bases aceitas |
|---|---|
| Salário base | só valor fixo mensal |
| Adicional | salário |
| DSR | salário, salário + adicionais |
| Provisão mensal | salário, remuneração |
| Encargo | salário, remuneração, remuneração + provisões |
| Benefício / Custo indireto | salário, remuneração |

* **Arredondamento**: cada item vira centavos **uma vez** (meio para cima); totais são somas de itens já arredondados.
* **Períodos** a partir do **total mensal** e dos *dias do mês* da unidade (`ConfiguracaoMaoDeObra.dias_mes`, padrão 30): dia = mês ÷ dias; semana = mês × 7 ÷ dias; quinzena = mês ÷ 2; bimestre/trimestre/semestre/ano = 2/3/6/12 × mês. Cada período é arredondado uma vez a partir do mensal (não da soma de períodos menores). Dia, semana e quinzena coincidem com `derivados_do_mes`. **Meses de tamanhos diferentes não são modelados**: usa-se o "mês comercial" configurado; o ano é 12 × mês, não 365 dias.
* **Hora** = total mensal ÷ `horas_mes` (**h/mês** da unidade, a mesma base mostrada na edição do colaborador, que foi preservada).
* **Vigência**: valem os itens `ativo`, com `inicio ≤ data` e `fim` vazio ou `≥ data`; vários salários cobrindo a mesma data não podem existir (validado), e se existirem vale o de início mais recente.
* **Exemplo** (salário R$ 3.000,00; adicional 30 % s/ salário; DSR 10 % s/ salário + adicionais; 13º 8,3333 % s/ remuneração; FGTS 8 % s/ remuneração + provisões; VR R$ 500,00; indireto R$ 1.200,00/ano): 3.000 + 900 + 390 + 357,50 + 371,80 + 500 + 100 = **R$ 5.619,30/mês**; dia R$ 187,31; ano R$ 67.431,60. (Percentuais ilustrativos do teste, **não** são recomendação.)

## Banco de dados

Migração `maodeobra.0003_funcoes_vinculos_simulacoes` (**aditiva**; só tabelas novas; cria e concede as permissões novas a Administrador e Gestor; reversível — o reverso remove as tabelas novas e **não** apaga nada do que já existia). Nenhuma tabela existente foi alterada. Multi-tenant: `cliente` é coluna nas tabelas-raiz; as filhas **derivam** a unidade do pai (sem coluna duplicada que possa divergir).

### Tabelas novas

**`maodeobra_funcaomaodeobra`** — função/cargo da unidade.

| Coluna | Tipo | Nulo | Finalidade |
|---|---|---|---|
| `id` | bigint PK | não | |
| `cliente_id` | FK `cliente_cliente` (PROTECT) | não | unidade dona (tenant) |
| `nome` | varchar(80) | não | único por unidade (`funcao_mo_nome_unico`) |
| `descricao` | varchar(255) | não (vazio) | |
| `cbo_codigo` | varchar(6) | não (vazio) | CBO, 6 dígitos (validador) |
| `cbo_descricao` | varchar(150) | não (vazio) | |
| `observacao` | varchar(255) | não (vazio) | |
| `ativo` | bool | não (padrão `true`) | |
| `criado_em`, `atualizado_em` | datetime | não | |

Índice `funcao_mo_cliente_ativo_idx (cliente, ativo)`.

**`maodeobra_componentefuncao`** — item de custo da função, com vigência.

| Coluna | Tipo | Nulo | Finalidade |
|---|---|---|---|
| `funcao_id` | FK função (CASCADE) | não | pai (define o tenant) |
| `categoria` | varchar(10) | não | `salario`, `adicional`, `dsr`, `provisao`, `encargo`, `beneficio`, `indireto` |
| `nome` | varchar(100) | não | |
| `tipo_valor` | varchar(10) | não | `fixo` ou `percentual` |
| `valor` | bigint | sim | centavos (tipo fixo) |
| `percentual` | decimal(8,4) | sim | taxa mensal em % (tipo percentual) |
| `base` | varchar(22) | não (`salario`) | base do percentual |
| `periodicidade` | varchar(6) | não (`mensal`) | `mensal` ou `anual` (÷ 12) |
| `personalizavel` | bool | não (`false`) | permite valor próprio por colaborador |
| `condicao` | varchar(255) | não (vazio) | condição/elegibilidade/fonte (premissa) |
| `inicio` / `fim` | date | não / sim | vigência |
| `ativo` | bool | não | |

Restrição `componente_valor_ou_percentual` (exatamente um dos dois preenchido). Índice `componente_funcao_vig_idx (funcao, categoria, inicio)`.

**`maodeobra_vinculofuncao`** — histórico de funções do colaborador.

| Coluna | Tipo | Nulo | Finalidade |
|---|---|---|---|
| `colaborador_id` | FK colaborador (PROTECT) | não | |
| `funcao_id` | FK função (PROTECT) | não | |
| `inicio` / `fim` | date | não / sim | vigência (`fim` vazio = atual) |
| `criado_por_id` | FK usuário (SET NULL) | sim | |
| `criado_em` | datetime | não | |

Restrição `vinculo_funcao_um_aberto` (no máximo um vínculo aberto por colaborador). Índice `vinculo_funcao_colab_ini_idx (colaborador, inicio)`. Como as FKs são PROTECT, colaborador/função com vínculo são **desativados**, não apagados.

**`maodeobra_personalizacaocolaborador`** — valor individual de um item personalizável, preso ao vínculo.

| Coluna | Tipo | Nulo | Finalidade |
|---|---|---|---|
| `vinculo_id` | FK vínculo (CASCADE) | não | |
| `componente_id` | FK item (PROTECT) | não | |
| `valor` / `percentual` | bigint / decimal(8,4) | sim | exatamente um preenchido (`personalizacao_valor_ou_percentual`) |
| `atualizado_em` | datetime | não | |

Única por `(vinculo, componente)` (`personalizacao_unica`).

**`maodeobra_simulacaopromocao`** — parâmetros de um cenário (o resultado não é gravado).

| Coluna | Tipo | Nulo | Finalidade |
|---|---|---|---|
| `cliente_id` | FK cliente (PROTECT) | não | tenant |
| `nome` | varchar(100) | não | |
| `tipo` | varchar(6) | não | `pessoa` ou `funcao` |
| `colaborador_id` | FK (PROTECT) | sim | tipo pessoa |
| `funcao_origem_id` / `funcao_destino_id` | FK função (PROTECT) | sim | origem (tipo função) e destino (opcional) |
| `novo_salario` | bigint | sim | centavos/mês |
| `ajuste_salario_percentual` | decimal(7,2) | sim | % sobre o salário atual (excludente com `novo_salario`) |
| `data_referencia` | date | não | data da comparação |
| `observacao` | varchar(255) | não (vazio) | |
| `criado_por_id` | FK usuário (SET NULL) | sim | |
| `criado_em`, `atualizado_em` | datetime | não | |

Índice `simulacao_cliente_data_idx (cliente, criado_em)`. A unidade do colaborador/funções escolhidos deve ser a da simulação (validado).

### Tabelas alteradas

Nenhuma. (`maodeobra_colaborador` continua igual; o vínculo com a função é uma tabela à parte.)

## Endpoints (API v1, Bearer, módulo `mao-de-obra`) e Swagger

Documentação: `/api/docs/` (Swagger UI), `/api/redoc/`, `/api/schema/` (somente administrador). Tag **mao-de-obra**. Respostas no envelope padrão (`data`, `message`, paginação em `itens`), dinheiro em **centavos**, percentuais em texto.

| Método e caminho | Função | Permissão |
|---|---|---|
| GET/POST `/mao-de-obra/funcoes/`, GET/PUT/PATCH/DELETE `/mao-de-obra/funcoes/{id}/` | Funções (DELETE: desativa se em uso) | `maodeobra.*_funcaomaodeobra` |
| GET/POST `/mao-de-obra/itens-de-custo/` (`?funcao=`), GET/PUT/PATCH/DELETE `…/{id}/` | Itens de custo (DELETE encerra o vigente) | `maodeobra.*_componentefuncao` |
| POST/PATCH `/colaboradores/` (+ `funcao_vinculada`, `funcao_inicio`) | Vincula/troca a função (mesma regra da tela) | `maodeobra.*_colaborador` |
| GET `/colaboradores/{id}/composicao/?data=` | Composição, períodos e hora | `view_colaborador` |
| PUT `/colaboradores/{id}/personalizacoes/` | Substitui as personalizações do vínculo atual | `change_colaborador` |
| GET/POST `/mao-de-obra/simulacoes/`, GET/PUT/PATCH/DELETE `…/{id}/`, GET `…/{id}/resultado/` | Cenários e resultado atual × simulado | `maodeobra.*_simulacaopromocao` |
| GET `/mao-de-obra/dashboard/?data=&comparar_com=&funcao=&periodo=` | Indicadores | `view_colaborador` |

Segurança: o tenant vem **sempre** do usuário (nunca do corpo); id de outra unidade responde **404** (função, item, simulação, colaborador) ou **400** quando referenciado dentro do corpo; campos calculados e `cliente` enviados no corpo são ignorados; listagens paginadas com limite do projeto; mudanças auditadas (criação/edição/exclusão com valores monetários).

## Implantação e rollback

* `python manage.py migrate maodeobra` (a migração só cria tabelas e concede permissões; sem dados a converter).
* Rollback: `python manage.py migrate maodeobra 0002` remove as cinco tabelas novas (perde funções, itens, vínculos e simulações cadastrados depois). Faça backup antes de produção.
* Nenhuma variável de ambiente nova. Opcional: ajuste *Horas por mês* e *Dias do mês* em *Regras de cálculo* (usados nos períodos e na hora).

## Partes mais complexas

* `composicao._montar`/`composicoes_em_lote`: reúne vínculo vigente, itens vigentes, personalização e a exceção do **salário** (que vive em `Colaborador.valor_mes` para preservar h/mês); em lote para o dashboard não fazer N+1.
* `ColaboradorForm.clean`/`save`: a coerência `valor_mes` × função (salário não personalizável sempre da função) e a troca de vínculo com histórico.
* `composicao._itens_simulados`: decide o que o cenário herda (mesma função mantém personalizações; função nova herda tudo da destino) e a precedência *novo salário > ajuste % > salário da função*.

## Como estender

* **Novo item de custo**: é só dado (cadastre na função). Para uma **nova categoria**: inclua em `custos.CATEGORIAS`/`ORDEM` (posição = ordem de cálculo) e em `BASES_POR_CATEGORIA`; acrescente o campo no `choices` do modelo (migração) e teste em `tests_funcoes.CustosPuroTests`.
* **Nova base de percentual**: `custos.BASES`, `calcular()` (`atualizar_bases`) e `BASES_POR_CATEGORIA`.
* **Tour**: telas novas usam `initTourModulo('<chave>')` e a chave precisa existir em `src/assets/js/tours-modulos.js` **e** em `TELAS` de `apps/tour/tests_modulos.py` (o teste confere ids e seletores). Passos cujo elemento não aparece (botão sem permissão) são pulados sozinhos.

## Limitações conhecidas e decisões

* Custo completo é **informativo** (não alimenta a hora de apontamentos/fichas), por decisão do cliente.
* Salário personalizado = `Colaborador.valor_mes`; salário de função com vigência futura **não** atualiza `valor_mes` sozinho (a atualização acontece ao salvar o colaborador). A composição usa o salário vigente na data quando o item não é personalizável **e** o cadastro tem outro valor, valendo o `valor_mes` apenas se o item for personalizável.
* "Sem função cadastrada" não é desfeito pela tela (escolher a opção vazia mantém o vínculo atual); para trocar, escolha outra função.
* Simulação não permite ajustar item a item (só salário e função de destino) nem "aplicar" o cenário; consumirá hoje os dados vigentes na data de referência.
* Dashboard e simulações usam o escopo de unidades do usuário (matriz vê as filiais com a permissão de itens de filial); a API opera só na unidade do usuário, como o resto da API de mão de obra.
* Cobertura de testes: `apps/maodeobra/tests_funcoes.py` (fórmulas, vigência, vínculo/histórico/personalização, simulação, dashboard, tenant/permissões, API e Swagger).
