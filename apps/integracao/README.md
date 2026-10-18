# Integrações e agenda (`apps/integracao`)

Conexões com **WhatsApp**, **Instagram**, **Google Agenda** e **iCloud (CalDAV)**, mais a **agenda** de compromissos do sistema. iFood e 99Food: **somente a estrutura** (basta criar um adaptador novo, ver abaixo).

> **IMPORTANTE — nada aqui foi homologado contra conta real.** Os conectores foram escritos a partir da documentação pública dos provedores e testados com o adaptador simulado (`teste`, só em DEBUG). A tela mostra "não homologado" em cada provedor. Antes de produção: testar com contas de homologação, credenciais próprias e os fluxos de aprovação de cada provedor (Meta para WhatsApp/Instagram, verificação OAuth do Google). Nenhuma integração deve ser anunciada como funcional sem essa validação.

## Como funciona

- **Credenciais**: cifradas (Fernet, biblioteca `cryptography`) com a chave de `INTEGRACAO_CRYPTO_KEYS`; nunca são exibidas, registradas em log, auditoria ou mensagem de erro. Em produção, sem a chave, o sistema recusa gravar credenciais. Rotação: coloque a chave nova na frente (separe por vírgula), rode `manage.py rotacionar_credenciais` e depois remova a antiga.
- **Rede (anti-SSRF)**: só HTTPS para host público, com limite de tamanho e tempo (`rede.py`).
- **Webhooks** (`/webhooks/<token>`, público): autenticados por assinatura HMAC + token da URL, **idempotentes** (`EventoWebhook`), com limite de chamadas por conexão/IP.
- **Mensagens**: fila de saída com reenvio e espera crescente (`manage.py enviar_mensagens_pendentes`, agende a cada minuto). Sem garantia de entrega instantânea. Idempotente por `chave`/`Idempotency-Key` na API. O contato (dado pessoal) nunca vai para log.
- **Agenda**: `manage.py sincronizar_integracoes` (ex.: a cada 15 min). Vencimentos de contas a pagar/receber vão do sistema para o calendário (valores em R$ só se "incluir valores" estiver ligado); **compromissos** sincronizam nos dois sentidos, e em conflito vence a alteração **mais recente**. Desativar um compromisso remove o evento externo.
- **OAuth Google**: `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET` no `.env`; sem eles a conexão fica indisponível. `renovar_credenciais` renova tokens a vencer.
- Registro de log por conexão (`LogIntegracao`) e auditoria de conectar, alterar credenciais, desconectar e mensagens.

## Permissões

| Perfil | Pode |
|---|---|
| Administrador | tudo |
| Gestor | conectar, alterar credenciais, testar, sincronizar, desconectar, enviar e ver mensagens, ver o log |
| Operador | agenda (ver, criar, alterar compromissos); **não** vê conexões nem mensagens |
| Visualizador | ver a agenda |

Módulos: `integracoes` (Configurações) e `agenda` (menu principal), ambos "essenciais" (dependem só da permissão). Tudo é da unidade do usuário; conexão de outra unidade devolve 404.

## API v1

`/integracoes/` (+ `provedores/`, `{id}/testar|sincronizar|desconectar/`, `{id}/credenciais/`), `/mensagens/` (POST idempotente), `/compromissos/` (CRUD). Credenciais são recebidas e **nunca devolvidas**.

## Provedor novo (ex.: iFood, 99Food)

Crie a classe em `adaptadores/` herdando de `Adaptador` (`base.py`), implemente o que o provedor suporta (testar, enviar mensagem, webhook, eventos) e acrescente-a ao `REGISTRO` em `adaptadores/__init__.py`. O núcleo não muda.

## Tabelas

`integracao_conexao`, `integracao_eventowebhook`, `integracao_mensagem`, `integracao_compromisso`, `integracao_itemsincronizado`, `integracao_logintegracao` (migração `integracao.0001`, aditiva; módulos em `modulo.0017`).

## Variáveis de ambiente

`INTEGRACAO_CRYPTO_KEYS`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `BASE_URL` (para montar o endereço de retorno OAuth e dos webhooks).

## Agenda: lembretes, resumo do dia e modal do calendário

**Lembretes** (`lembretes.py`, comando `manage.py enviar_lembretes_agenda`): cada compromisso tem `lembrete_minutos` (nulo = sem; 0 = na hora; 15; 60; 1440). O comando envia pelo sino/push web (`apps.notificacao.enviar_para_usuarios`) aos usuários **da unidade do compromisso** que têm `integracao.view_compromisso` e o módulo `agenda`. Idempotente: antes de enviar, reserva com `UPDATE ... WHERE lembrete_enviado_em IS NULL`; reagendar (mover no calendário ou mudar início/antecedência) zera a marca. Compromissos concluídos/inativos ou que começaram há mais de 24 h são ignorados. **Agendar a cada 5 min:** cron `*/5 * * * * python manage.py enviar_lembretes_agenda` ou Agendador de Tarefas do Windows (ação: `.venv\Scripts\python.exe manage.py enviar_lembretes_agenda`, repetir a cada 5 minutos).

**Resumo ao entrar**: sinal `user_logged_in` (`apps.py`) chama `resumo_do_dia`: uma notificação por usuário/dia ("N para hoje e M em atraso"), só da própria unidade; falhas são registradas e nunca impedem o login.

**Calendário**: clicar no dia abre o modal já com a data; clicar no evento edita; concluir/reabrir e excluir (lógico) no modal. Salvar usa `agenda/salvar` (cria) e `agenda/<id>/salvar` (edita), POST com CSRF e o mesmo `CompromissoForm` (validação, auditoria e escopo de unidade da tela completa). Permissões: `add_compromisso`/`change_compromisso`. Arrastar e soltar e a visão em tabela continuam iguais.

| Coluna nova `integracao_compromisso` | Tipo | Nulo | Padrão | Finalidade |
|---|---|---|---|---|
| `lembrete_minutos` | int ≥ 0 | sim | null | antecedência do lembrete |
| `lembrete_enviado_em` | datetime | sim | null | quando o lembrete saiu (idempotência) |

Migração `0003_compromisso_lembrete`: colunas nulas, sem efeito nos dados existentes (nenhum compromisso antigo ganha lembrete); reversível. API: `lembrete_minutos` em `/api/v1/compromissos/`. Testes: `tests_lembretes.py`.

## Google Tarefas (no lugar do Google Keep)

**Por que não o Keep:** a documentação oficial da Google Keep API (consultada em 18/10/2026) a descreve para **administradores de domínio Google Workspace** (delegação em todo o domínio), com operações de criar/listar/excluir notas e **sem edição**; não há confirmação de uso por contas Google comuns. Foi aprovada a alternativa **Google Tarefas** (Tasks API v1, escopo `https://www.googleapis.com/auth/tasks`), que atende contas comuns e tem leitura e escrita.

**O que sincroniza** (`adaptadores/agenda.py::GoogleTasks`): os compromissos do tipo **Tarefa** ⇄ tarefas da lista configurada (`@default` = principal), nos dois sentidos: título, observações, data e **concluído**. Vencimentos de contas e demais tipos **não** vão para o Google Tarefas. Tarefa criada no Google (com data) vira Tarefa na agenda (origem "externa"); tarefa sem data é ignorada.

**Regra de conflito:** a mesma da agenda — vence a alteração **mais recente**; empate, o sistema. **Limite da API:** o campo `due` guarda só a data (a hora é descartada), então a tarefa é "dia inteiro" no Google; ao importar, a data vira dia inteiro. Excluir no sistema remove do Google; excluir no Google desativa a tarefa local (dentro da janela de sincronização).

**Como conectar:** em Integrações, "Conectar com o Google" no cartão *Google Tarefas* (mesmo OAuth e mesma URL de retorno do Google Agenda; habilite a **Tasks API** no projeto do Google Cloud e adicione o escopo na tela de consentimento — o Google pode exigir verificação do app para uso público). Sem `GOOGLE_OAUTH_CLIENT_ID/SECRET` a conexão fica indisponível com mensagem. A rotina é a mesma `sincronizar_integracoes` (agendar a cada 15 min).

**Status:** implementado pela documentação, **não homologado** com conta real (⚠ `@default` como id de lista não aparece na página de `insert`; `dueMin/dueMax/showHidden` confirmados na página de `list`). Docs: https://developers.google.com/workspace/tasks/reference/rest/v1/tasks · https://developers.google.com/workspace/keep/api/guides.

Mudanças de código: `Adaptador.so_tarefas`/`sincroniza_conclusao` (base), `services.eventos_locais` (filtra e inclui `concluido` só nesses adaptadores; o hash dos outros provedores não muda) e OAuth parametrizado por provedor (lista fechada `PROVEDORES_GOOGLE`). Sem tabelas novas. Testes: `tests_tarefas.py`.
