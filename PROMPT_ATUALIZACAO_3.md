# PROMPT — ATUALIZAÇÃO DO SISTEMA FATURANDO CERTO (custos por segmento, Kanban, Agenda, Pagar.me, Google Keep)

Atue como uma IA de desenvolvimento responsável por **atualizar** este projeto Django (Sistema Faturando Certo). Execute as alterações abaixo **sem alterar desnecessariamente o que já funciona**.

---

## 0. REGRA DE OURO DAS PERGUNTAS (leia primeiro)

1. **Todos os questionamentos devem acontecer UMA ÚNICA VEZ, no início da avaliação**, antes de editar qualquer arquivo ou banco.
2. Faça a avaliação (seção 2), junte **todas** as dúvidas e decisões pendentes e apresente-as **juntas, numa só rodada** (use no máximo 4 perguntas por vez, agrupando por tema; se precisar de mais, use uma segunda rodada **ainda antes de começar**).
3. Para cada pergunta, **recomende uma resposta padrão** (a melhor opção) e explique em uma linha por quê.
4. Depois das respostas, apresente um **plano conciso** (módulos, arquivos, endpoints, migrações, variáveis de ambiente) e **execute até o fim sem pausas** para novos questionamentos. Se surgir um bloqueio realmente impeditivo, conclua o que for independente dele e apresente as perguntas restantes juntas no final, explicando o bloqueio.
5. **Nunca invente** credenciais, IDs, chaves, URLs, valores contratados, nem comportamento de API externa. Se algo da documentação externa não puder ser confirmado, diga e implemente de forma configurável. **Nunca declare uma integração externa como funcionando sem evidência verificável** (resposta real ou teste de homologação).

Perguntas que você **deve** considerar levantar na rodada inicial (ajuste conforme a avaliação):
- **Pagar.me**: conta/ambiente (sandbox x produção), versão da API (v5 recomendada), formas de pagamento a habilitar (cartão, PIX, boleto), política de carência/bloqueio (quantos dias de tolerância após falha), o que acontece com os dados quando o acesso é bloqueado, nota fiscal da assinatura, e se o plano é por **Cliente (tenant)** ou por **unidade (matriz/filial)**.
- **Google Keep**: a API oficial do Keep é restrita (hoje voltada a Google Workspace e com escopos próprios). Pergunte qual conta/domínio será usado e **valide na documentação oficial atual** a viabilidade antes de implementar; ofereça alternativa (ex.: Google Tasks/Calendar) se o Keep não for viável para contas comuns.
- **Custos por segmento**: como o "valor cobrado/faturado" deve ser derivado do custo operacional (markup/lucro desejado, base de horas/dias do mês — o sistema já tem `horas_mes`/`dias_mes` na mão de obra), e se o indicador é por unidade, por segmento ou ambos.
- **Kanban**: quais entidades viram cartões/colunas (ex.: pedidos, tarefas, não conformidades) e quem pode mover.

---

## 1. PRINCÍPIOS (valem para TODAS as alterações)

### 1.1 Preservação
- **Não reescreva** áreas estáveis. Reaproveite serviços, formulários, mixins, decorators e padrões existentes. Antes de criar algo, procure se já existe.
- Mantenha compatibilidade com telas, API v1, apps mobile e **dados existentes**. Se uma mudança incompatível for inevitável, **versione** (`/api/v2/…`) e documente a transição.
- Toda **coluna nova em tabela existente** precisa de `null=True` ou `db_default` (senão `tests_migracao` quebra e dados antigos não migram). Migrações **aditivas e reversíveis**; nunca apague/altere dados sem plano.
- Não remova funcionalidades sem justificativa técnica.

### 1.2 Código limpo e enxuto
- **Quanto menos complicado, melhor.** Código simples, funções pequenas, nomes descritivos, DRY, KISS. Sem abstrações, dependências ou camadas sem benefício prático. Prefira a biblioteca padrão e o que já está no `requirements.txt`.
- Regras de negócio **centralizadas em services** (não em views/templates). Views finas, templates sem regra.
- Siga o estilo do código vizinho (idioma pt-BR em textos de interface, UTF-8, acentuação correta).

### 1.3 Comentários e documentação (obrigatório)
- **COMENTE TODO O CÓDIGO CRIADO OU ALTERADO**: finalidade, regra de negócio, decisões não óbvias, segurança e unidades (centavos, fuso). Não comente o óbvio linha a linha; explique o **porquê**.
- Docstring em todo módulo, classe e função pública (o que faz, parâmetros, o que devolve, efeitos colaterais).
- **Crie/atualize um README por módulo** (`apps/<modulo>/README.md`) explicando o módulo inteiro: arquitetura, fluxo, arquivos e responsabilidades, endpoints, permissões, **partes mais complexas do código destacadas** (com diagrama ou exemplo simples quando ajudar), decisões e limitações, como testar e operar.
- **Detalhe no README, em tabela, TODAS as tabelas e colunas criadas ou alteradas no banco**: nome, tipo, nulabilidade, padrão, índices/chaves/relacionamentos, regra de tenant, finalidade e **impacto da migração** (como fica para dados existentes; reversibilidade).
- Para integrações externas, crie um README de configuração/operação separado (passo a passo no painel do provedor, variáveis de ambiente, rotação de credenciais, diagnóstico, rollback), com **links diretos para a documentação oficial consultada** e a data da consulta.

### 1.4 Segurança e multi-tenant (inegociável)
- Cadeia de acesso do projeto: **Usuário → Cliente (tenant, com matriz/filiais) → Segmento → Módulo habilitado → Permissão do perfil → Dados.** Use SEMPRE as funções de `web_project/acesso.py` (`clientes_dos_itens`, `clientes_acessiveis`, `administrador_global`…). Nunca repita regra de acesso em view/template.
- **Derive o tenant do contexto autenticado no servidor.** Nunca confie em `cliente_id` vindo do navegador/app. Um tenant não pode ler, alterar, excluir, inferir a existência ou exportar dados de outro (respostas "não existe" e "sem permissão" devem ser iguais: 404).
- Toda URL usa `module_required(slug, permissão)` ou, na API, `PermissaoPorAcao`; **esconder menu não é controle de acesso**. Áreas "só admin" = `administrador_global` ou superusuário conforme a regra definida na avaliação, validado na **rota E no serviço**.
- **Segredos** (tokens, chaves, webhooks secrets) só por variável de ambiente ou cifrados com `apps/integracao/cripto.py` (Fernet). Nunca em código, banco em claro, logs, URLs, README com valores reais, nem na resposta da API.
- Valide e limite toda entrada (tamanho, tipo, faixa); use ORM (sem SQL cru); escape de saída; CSRF nas telas; rate limit em endpoints sensíveis (ver `auth/ratelimit.py` e `throttle_scope` da API); **idempotência** onde o fluxo pode repetir (webhooks, pagamentos, criação de cobrança).
- **Auditoria** (`apps/auditoria/utils.py`): registre criação, edição, exclusão e ações sensíveis (ator, tenant, entidade, ID), sem segredos nem dados de cartão.
- Dinheiro: **inteiro em centavos** (nunca float). Datas com fuso (`timezone`). Cartão: **nunca** trafegue/guarde número de cartão — use tokenização/checkout do provedor.

### 1.5 Padrão para criar um módulo novo (siga exatamente)
`INSTALLED_APPS` → `config/urls.py` → `apps/api/urls.py` → entrada em `seed_modulos.MODULOS_PADRAO` + migração em `apps/modulo` (como `0020/0021`) → `apps/acl/padroes.py` (perfis padrão, `ADMIN_APPS`/`SCREEN_APPS`, permissões personalizadas em `EXTRAS`) → migração `RunPython` que cria e concede permissões (padrão das migrações `pdv.0004`/`insumoglobal.0002`) → módulos devem aparecer em **Módulos, Papéis e Permissões especiais do usuário**. Tenant-específico: `ClienteScopedMixin`/`ListaPorUnidadeMixin`. Auditoria: `AuditoriaFormMixin`. Selects com pesquisa (select2) só disparam `change` pelo jQuery: use `jQuery(el).on('change', …)`.

### 1.6 Ferramentas e validação
- Python do projeto: `.venv/Scripts/python.exe` com `PYTHONUTF8=1`. Testes: `manage.py test <app>`; a suíte completa leva ~35 min — rode em segundo plano e **não edite settings/urls/migrações enquanto ela roda**.
- Escreva testes para: regra de negócio, **isolamento entre tenants**, permissões, erros e idempotência. Integrações externas: teste com **cliente simulado (mock)**; nunca chame serviço real nos testes.
- Antes de concluir: `manage.py check`, `makemigrations --check`, testes dos apps afetados e verificação visual no navegador das telas novas (quando possível).
- Só faça **commit** quando solicitado; ao final, informe o que foi feito, o que ficou pendente e o que depende de configuração externa.

---

## 2. AVALIAÇÃO INICIAL (antes de qualquer edição)

1. Inspecione: `apps/custo` (custo operacional, `total_operacional`, `distribuicao_por_categoria`), `apps/maodeobra` (já calcula hora/dia/semana/quinzena a partir do valor mensal; `ConfiguracaoMaoDeObra.horas_mes/dias_mes`), `apps/modulo` (Segmento, `tipo_precificacao`), `apps/dashboard` (indicadores e `services.py`), `apps/precificacao`, `apps/integracao` (adaptadores, `cripto.py`, webhooks, agenda/Google Calendar), `apps/notificacao` (sino, push web, `enviar_para_usuarios`), `apps/cliente` (tenant, abas do cadastro), `apps/api` (padrão `RecursoViewSet`, `PermissaoPorAcao`, `documentar`, envelope de resposta) e `apps/fiscal`/`apps/imagens` como exemplos de módulo com README e testes.
2. Identifique o que **já existe** para cada item (ex.: Kanban no template: `src/assets/js/app-kanban.js` e `src/assets/vendor/libs/jkanban`; agenda com calendário FullCalendar, arrastar e soltar, tipos e concluir/excluir já feitos).
3. Liste os **endpoints da API v1 que faltam** para tudo que esta atualização cria **e** para funcionalidades já existentes que o app mobile precisará (compare as telas web com `apps/api/urls.py`). Proponha-os na rodada de perguntas.
4. Consulte a **documentação oficial atualizada** de cada serviço externo (Pagar.me, Google) e cite links/datas. Não use memória para detalhes de API.
5. Faça a rodada única de perguntas (seção 0) e só então apresente o plano.

---

## 3. ITENS A IMPLEMENTAR

### 3.1 Custo operacional → valores a cobrar/faturar por segmento (+ dashboard)
- Crie **funções de reaproveitamento** (um único módulo de serviço, funções puras e testáveis, valores em centavos) que, a partir do **custo operacional**, calculem o valor a ser **cobrado/faturado** em cada período: **mensal, quinzenal, semanal, diário e por hora**, **para cada segmento** (Segmento tem `tipo_precificacao`: serviço, produto, ambos).
- **Reaproveite** a convenção já existente de `apps/maodeobra/calculo.derivados_do_mes` (horas/mês e dias/mês configuráveis por unidade) — não duplique fórmulas; extraia para um ponto comum se necessário, sem quebrar a mão de obra.
- Documente no README as **fórmulas, premissas, arredondamento (uma vez, meio para cima) e limites**; deixe parâmetros (margem, horas/dia do mês) configuráveis por unidade, **sem fixar valores no código**.
- **Disponibilize os indicadores no dashboard** (apps/dashboard), respeitando escopo de unidade/tenant e permissão. Expõe também na API (`/api/v1/painel/…`) e no Swagger.
- Testes: fórmulas com exemplos numéricos, arredondamento, unidade sem custo, isolamento de tenant.

### 3.2 Kanban (somente administrador)
- Crie o **Kanban já existente no template** (reaproveite `app-kanban.js`/`jkanban` já presentes em `src/assets`; **não adicione dependência nova**). Acesso **somente administrador** (regra de papel definida na avaliação), validado em rota, serviço e API.
- Persistência em banco (colunas e cartões, ordem, responsável, datas), **sem estado só no navegador**. Mover cartão = endpoint com validação de permissão, tenant e ordem; auditar movimentações.
- Defina na avaliação as entidades do quadro; comece simples (colunas configuráveis + cartões com título/descrição/responsável).

### 3.3 Agenda
- **Notificações de agenda**: (a) ao **entrar no sistema** (resumo de compromissos/tarefas do dia e atrasados, uma vez por sessão/dia, sem repetir em cada página) e (b) **no horário selecionado** (lembrete configurável por compromisso, ex.: na hora, 15 min, 1 h, 1 dia antes). Use `apps/notificacao` (sino/push web já existem) e um **comando agendável** (`manage.py enviar_lembretes_agenda`, idempotente: nunca envie o mesmo lembrete duas vezes; respeite fuso e escopo de unidade). Documente como agendar (cron/Agendador de Tarefas).
- **Clicar na data do calendário** deve permitir **adicionar** (abre formulário/modal já com a data) e **editar** (clicar no evento) sem sair da visão de calendário, mantendo a tabela disponível. Reaproveite `CompromissoForm`, permissões `integracao.add_/change_compromisso` e o escopo de unidade; mantenha o arrastar e soltar existente. Ações com CSRF e validação no servidor.
- Se criar campos novos (ex.: antecedência do lembrete, lembrete enviado em), documente colunas e use `db_default`.

### 3.4 Integração Pagar.me (assinaturas) — usando TODA a documentação, respeitando as regras dela
Fonte: <https://docs.pagar.me/> (confirme a versão atual da API, autenticação, ambientes, objetos e **webhooks** na documentação oficial **antes** de codificar; liste os links consultados).
- **Planos** (valores configuráveis, não fixos no código; criados/sincronizados no Pagar.me): **recorrente mensal R$ 59,90** e **anual R$ 429,00** (R$ 59,90 = 5990 centavos; R$ 429,00 = 42900).
- Cubra as possibilidades da documentação relevantes a assinaturas: **clientes, planos, assinaturas, cobranças/faturas, cartão (com tokenização/checkout do provedor), PIX, boleto, cancelamento, reembolso/estorno, troca de plano, retentativas, webhooks**, e o que mais a documentação oferecer que faça sentido ao produto — apresentando na rodada inicial o que será incluído e o que ficará de fora, com justificativa.
- **Regras lógicas do plano** (centralize em um **serviço de assinatura**, com **máquina de estados** documentada e testada), por exemplo:
  - estados: `teste/pendente → ativa → em_atraso (carência) → bloqueada → cancelada`, e `expirada` para o anual;
  - **renovação mensal**: cobrança recorrente criada/renovada pelo provedor; o sistema **só confia no webhook validado** (assinatura/segredo) para marcar pagamento;
  - **não pagou → carência configurável → bloqueia o acesso** do tenant (módulos operacionais), mantendo **leitura/exportação mínima e a tela de regularização/pagamento acessível**; ao pagar, **desbloqueia automaticamente**;
  - **plano anual**: validade de 12 meses, aviso antes de vencer, renovação, **upgrade/downgrade** com regra de proporcionalidade definida na avaliação;
  - **cancelamento** pelo cliente/admin, **reembolso** conforme regras do provedor e do negócio;
  - **idempotência de webhooks** (guarde o ID do evento; reprocessar não pode duplicar efeitos), ordem fora de sequência, reenvio e falhas parciais com **estratégia de compensação**;
  - verificação periódica (comando agendável) que reconcilia o estado local com o provedor.
- **Bloqueio de acesso**: aplique em **um único ponto** (ex.: middleware/decorator central integrado a `modulos_habilitados`/`module_required`), nunca espalhado; **superusuário/proprietário da plataforma nunca é bloqueado**; mensagens claras; cobre telas **e API** (código HTTP apropriado, ex. 402/403 documentado no Swagger).
- **Segurança**: chave secreta do Pagar.me só em variável de ambiente/cifrada; **não registre dados de cartão**; valide a **assinatura dos webhooks**; endpoint de webhook público porém restrito (verificação, limite de tamanho, resposta rápida); logs sem payloads sensíveis.
- **Aba "Pagamentos/Assinatura" nos dados do cliente** (cadastro de Cliente): plano atual, status, próxima cobrança, **histórico de cobranças e pagamentos** (data, valor, forma, status, ID da transação — sem dados sensíveis), ações permitidas (ver/segunda via de boleto/PIX, cancelar, trocar plano) conforme permissão, e linha do tempo de eventos. Visível apenas a quem tem permissão no tenant; **outros tenants nunca**. Inclua exportação se fizer sentido.
- Tabelas sugeridas (adapte à avaliação): plano, assinatura (cliente, plano, status, datas, id externo), cobrança/pagamento (assinatura, valor, forma, status, id externo, datas), evento de webhook (id externo único, tipo, processado em). Documente cada coluna no README.
- Testes com **cliente Pagar.me simulado**: criação de assinatura, renovação, falha→carência→bloqueio→regularização, anual, cancelamento, webhook duplicado/inválido/fora de ordem, isolamento de tenant e permissão da aba.
- Entregue um **README de configuração do Pagar.me** (conta, chaves sandbox/produção, webhook, URLs, variáveis, testes em sandbox, checklist de homologação, rollback) e indique claramente o que **depende de ação externa** e **não foi verificado**.

### 3.5 Integração Google Keep
- **Antes de codificar**, valide na documentação oficial vigente se a **Google Keep API** atende ao caso (hoje costuma ser restrita a contas Google Workspace/administradores e escopos específicos) e **diga o que é possível**. Siga o padrão de `apps/integracao` (adaptador, conexão, credenciais cifradas, OAuth, log sem dados sensíveis, sincronização idempotente e comando agendável, renovação de token).
- Defina o que sincroniza (ex.: tarefas/lembretes da agenda ⇄ notas/listas), em qual sentido e a regra de conflito (reaproveite a regra de "mais recente vence" da agenda). Se o Keep não for viável, implemente a **alternativa aprovada** na rodada inicial e documente a limitação. **Nunca simule** sincronização; sem credenciais válidas, a conexão fica indisponível com mensagem clara.

---

## 4. API (MOBILE) E SWAGGER

- Analise e **crie todos os endpoints que faltam** (itens acima + lacunas encontradas na avaliação), seguindo exatamente o padrão da API v1: autenticação por token com expiração, envelope `{success, message, data|errors}`, paginação, permissões por ação, `throttle_scope` para endpoints sensíveis, tenant derivado do servidor, **sem expor** IDs internos, caminhos, segredos nem a existência de dados de outro tenant.
- **Atualize o Swagger/OpenAPI** (drf-spectacular): tags, descrições em português, parâmetros, exemplos **seguros** (sem credenciais reais), códigos de erro (inclusive bloqueio por assinatura) e regras de autorização. A documentação em `/api/docs/` é restrita a administradores.
- Webhooks e rotas públicas ficam **fora** do Swagger de apps ou claramente marcados, com a explicação de autenticação por assinatura.
- Teste cada endpoint: autenticação, permissão, **isolamento de tenant**, validação, paginação e idempotência.

---

## 5. ENTREGA FINAL (checklist)

1. O que foi implementado e o que ficou pendente.
2. Módulos/arquivos/funções/endpoints criados ou alterados.
3. **Tabelas e colunas alteradas**, com as migrações e o impacto nos dados existentes.
4. Como o multi-tenant e o acesso "só administrador" estão protegidos.
5. Configurações feitas na aplicação **x** etapas externas (painel do Pagar.me, Google, agendador de tarefas, variáveis de ambiente).
6. Validações executadas e resultados (testes, `check`, `makemigrations --check`, verificação visual).
7. Limitações conhecidas (inclusive o que **não** pôde ser verificado contra serviços reais).
8. READMEs atualizados e coerentes com o código.

Considere o trabalho concluído somente quando **código, documentação, API/Swagger e testes** estiverem coerentes. Nunca declare conexão externa, cobrança, sincronização ou configuração confirmadas sem evidência verificável.
