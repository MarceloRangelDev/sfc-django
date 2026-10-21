# apps/notificacao — Notificações (Web Push, pronto para FCM)

## Objetivo

Enviar avisos aos usuários (todos, por segmento ou usuários específicos), guardar o **histórico** (entrega e leitura) e exibir a **caixa de entrada** no sino da navbar. A **regra de negócio** (quem recebe, histórico, leitura) é separada do **mecanismo de entrega** (`canais.py`), então trocar ou somar canais não mexe no resto.

```text
Web     └── CanalWebPush  (django-webpush)      ← implementado
Mobile  └── CanalFCM      (Firebase Cloud Msg)  ← reservado em canais.py
```

## Estrutura

| Arquivo | Papel |
|---|---|
| `models.py` | `Notificacao` (a mensagem) e `DestinatarioNotificacao` (uma pessoa: entrega + leitura). |
| `services.py` | `resolver_destinatarios`, `enviar_notificacao`, `marcar_como_lida`, `marcar_todas_como_lidas`. |
| `canais.py` | `CanalEntrega` (interface), `CanalWebPush`, `CanalFCM` (stub), lista `CANAIS`, `entregar()`. |
| `forms.py` | `NotificacaoForm`: segmentos/usuários oferecidos = só os do escopo do solicitante; link só interno. |
| `views.py` | Nova (2 etapas), histórico (filtros + paginação), minhas, abrir (marca lida), inscrição push. |
| `urls.py` | Rotas + `service-worker.js` na raiz. |
| `templates/` | `notificacao_form`, `notificacao_historico`, `notificacao_minhas`. |
| `management/commands/gerar_chaves_vapid.py` | Gera as chaves VAPID para o `.env`. |
| `src/assets/js/notificacoes.js` | Registra o service worker e a inscrição de push do navegador. |
| `tests.py` | Envio, escopo, entrega, leitura, filtros, paginação. |

A navbar (`templates/layout/partials/navbar/navbar.html`) lê `notificacoes_nao_lidas`, `notificacoes_recentes` e `push_habilitado` do context processor `config.context_processors.notificacoes_navbar`.

## Fluxo de envio

```text
POST /notificacoes/nova (formulário)           → NovaNotificacaoView
   1ª etapa: valida e mostra a CONFIRMAÇÃO (título, mensagem, tipo, quantidade estimada) — nada é gravado
   2ª etapa (confirmado=1): services.enviar_notificacao
        [transaction.atomic] Notificacao + DestinatarioNotificacao (bulk) + auditoria (criar/notificacao)
        depois, por destinatário: canais.entregar() → status_entrega / entregue_em / erro_entrega
```

- **Destinatários** (`resolver_destinatarios`): sempre dentro de `usuarios_acessiveis(solicitante)` (estrutura matriz/filiais; Admin: todos), ativos e com cliente. Escolher um usuário/segmento de fora do escopo é rejeitado pelo formulário **e** ignorado pelo serviço.
- **Entrega**: basta **um** canal entregar. Sem dispositivo inscrito → `falha` com a mensagem do motivo; o usuário ainda vê a notificação no sino (ler no sistema também marca como *entregue*).
- **Leitura**: clicar no item chama `notificacao-abrir` → grava `lida_em`, e redireciona ao `link` (só caminhos internos). Só abre notificações **do próprio usuário** (outro id → 404).

## Atualização automática do sino

`src/assets/js/notificacoes-sino.js` consulta `GET /notificacoes/resumo` (login obrigatório, sem parâmetros: sempre a caixa do **próprio** usuário) a cada 20 s, só com a aba visível (e na hora ao voltar a ela), com espera crescente após falhas. A resposta traz o contador e o HTML da lista (`templates/layout/partials/navbar/notificacoes_itens.html`, o mesmo usado na página, com escape automático). Se o endpoint falhar ou a sessão expirar, o sino segue como foi renderizado. Escolhido no lugar de WebSocket/SSE porque o deploy é gunicorn síncrono, sem Channels/Redis.

## Web Push — configuração

```bash
python manage.py gerar_chaves_vapid    # cole as 3 linhas no .env
```

`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL`. Sem as chaves o sistema funciona; apenas o push do navegador fica desligado (o botão "Ativar notificações" não aparece). Push exige **HTTPS** em produção (`localhost` é aceito em desenvolvimento).

O servidor faz requisições HTTP para o `endpoint` da inscrição; por isso `InscricaoPushView` só aceita endpoints **https** dos serviços de push dos navegadores (`PROVEDORES_PUSH`: Google/FCM, Mozilla, Windows, Apple). Isso evita que um usuário aponte o servidor para um serviço interno (SSRF). Para suportar outro provedor, acrescente o domínio em `views.py`.

A view de inscrição é própria (`InscricaoPushView`): exige login, protege com CSRF e só grava para o usuário logado (a view padrão do pacote é isenta de CSRF e aceita anônimos, por isso não é usada).

## Preparação para FCM

1. Guardar o token do dispositivo (nova tabela `DispositivoMobile(usuario, token)`).
2. Implementar `CanalFCM.enviar` chamando a API do FCM.
3. Incluir `CanalFCM()` em `CANAIS`. Nada em `services.py`, views ou templates muda.
4. Para volumes altos, mover o laço de entrega de `enviar_notificacao` para uma fila (Celery/RQ).

## Banco de dados

### `notificacao_notificacao`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `titulo` | varchar(120) | sim | — | — |
| `mensagem` | text | sim | até 500 caracteres | — |
| `link` | varchar(200) | não | Caminho interno aberto ao clicar | — |
| `tipo_destinatario` | varchar(10) | sim | `todos`, `segmento`, `usuarios` | — |
| `criado_por_id` | FK | não | Quem enviou | `auth_user` (SET NULL) |
| `criado_em` | datetime | sim | — | — |

M2M `notificacao_notificacao_segmentos` (→ `modulo_segmento`) e `notificacao_notificacao_usuarios_alvo` (→ `auth_user`).

### `notificacao_destinatarionotificacao`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `notificacao_id` | FK | sim | — | `notificacao_notificacao` (CASCADE) |
| `usuario_id` | FK | sim | Destinatário | `auth_user` (CASCADE) |
| `status_entrega` | varchar(10) | sim | `pendente`, `entregue`, `falha` | — |
| `entregue_em` | datetime | não | Quando foi entregue | — |
| `erro_entrega` | varchar(255) | não | Motivo da falha | — |
| `lida_em` | datetime | não | **Nulo = não lida** | — |
| `criado_em` | datetime | sim | Data de envio | — |

Único: (`notificacao`, `usuario`). Índice: (`usuario`, `lida_em`) — usado pelo contador do sino. Tabelas do pacote: `webpush_subscriptioninfo`, `webpush_pushinformation`, `webpush_group`.

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| novo canal de entrega | `canais.py` (nova classe + `CANAIS`) |
| regra de quem recebe | `services.resolver_destinatarios` |
| filtros do histórico | `views.HistoricoView.get_queryset` + `notificacao_historico.html` |
| quantas aparecem no sino | `config/context_processors.py::notificacoes_navbar` |
| quem pode enviar/ver histórico | permissões `notificacao.add/view_notificacao` (perfil) |

## Notificações geradas pelo sistema

`services.enviar_para_usuarios(titulo, mensagem, link, usuarios)` cria e entrega uma notificação **sem solicitante** (alertas de estoque, por exemplo): o chamador define os usuários e garante o escopo (ex.: só a própria unidade). Reaproveita a mesma entrega por canais (`_entregar`) e o histórico de `DestinatarioNotificacao`; não gera auditoria de criação, pois não é ação de usuário.


## Sino com rolagem e retenção das lidas (48 h)

**Sino.** A lista da navbar (`#sino-lista`) mostra as 10 notificações mais recentes (`resumo_do_usuario(limite=10)`). Com **mais de 3** itens ganha barra de rolagem (`ajustarRolagem()` em `src/assets/js/notificacoes-sino.js` mede a altura dos 3 primeiros itens com o menu aberto e a usa como altura máxima; `#sino-lista` tem `overflow-y: auto` em `demo.css`); com 3 ou menos continua com a altura natural. O contador de não lidas é sempre o total real, não o das 10 listadas.

**Retenção.** Uma notificação **lida** é removida **48 horas depois do momento em que foi lida** (`DestinatarioNotificacao.lida_em`; constante `RETENCAO_LIDAS` em `models.py`). Não lidas **nunca** expiram e o prazo só começa quando `lida_em` é gravado. A regra vale por destinatário (cada pessoa tem a sua leitura e o seu prazo).

- **Remoção no servidor, independente de tela aberta:** `python manage.py limpar_notificacoes_lidas` → `services.limpar_lidas_expiradas()` apaga os destinatários expirados e as `Notificacao` que ficaram sem nenhum destinatário. **Agende a cada hora** (cron `0 * * * * python manage.py limpar_notificacoes_lidas` ou Agendador de Tarefas do Windows). É idempotente e seguro repetir.
- **Filtro nas consultas:** `DestinatarioNotificacao.objects.vigentes()` exclui as expiradas; sino, "minhas notificações", abrir e histórico o usam. Assim uma notificação expirada nunca aparece, nem pode ser aberta (404), mesmo que o agendamento ainda não tenha rodado.
- **Escopo preservado:** continua tudo filtrado por `usuario=request.user` (sino, minhas, abrir) ou por `usuarios_acessiveis` (histórico do administrador); a limpeza só atinge linhas com `lida_em` preenchido e vencido.
- **Banco:** nenhuma coluna nova foi necessária — `lida_em` já existia e já era gravada ao abrir/marcar como lida. Registros antigos já lidos têm `lida_em` real, então expiram 48 h após a leitura de verdade (com a primeira execução do comando, os lidos há mais de 48 h são apagados; não há tabela nem migração a aplicar). Não há exclusão em cascata de outras tabelas além da mensagem órfã.
- Consequência: o **histórico de envios** (tela do administrador) deixa de listar destinatários cujas notificações já foram lidas há mais de 48 h.

Testes: `python manage.py test apps.notificacao` (inclui `RetencaoDasLidasTests`).

### API (app mobile)

`GET /api/v1/notificacoes/` (`?situacao=lidas|nao-lidas`) · `GET /api/v1/notificacoes/resumo/` · `POST /api/v1/notificacoes/{id}/ler/` · `POST /api/v1/notificacoes/ler-todas/`. Só login; cada usuário alcança apenas as próprias (outro id = 404); as expiradas não aparecem. Código em `apps/api/notificacoes.py`. O envio de notificações continua só pela tela.
