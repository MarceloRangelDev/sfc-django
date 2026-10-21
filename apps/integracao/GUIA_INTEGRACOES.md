# Guia passo a passo das integrações

Este guia ensina, para **cada integração** do sistema, onde gerar as chaves no site do provedor, **onde colocá-las no sistema** e quais cuidados tomar. Para a parte técnica (arquitetura, tabelas, API), veja o [README.md](README.md).

> **Aviso importante:** os conectores foram escritos a partir da documentação pública e **ainda não foram homologados com contas reais** (a tela mostra "não homologado"). Os sites da Meta e do Google mudam menus e nomes com frequência: se algum botão citado aqui estiver em outro lugar, procure pelo nome na busca do painel do provedor. Teste sempre com uma conta de homologação antes de usar em produção.

## Índice

0. [Antes de tudo (vale para todas)](#0-antes-de-tudo-vale-para-todas)
1. [WhatsApp Business — API oficial da Meta](#1-whatsapp-business--api-oficial-da-meta)
2. [WhatsApp por provedor intermediário](#2-whatsapp-por-provedor-intermediário)
   - [2A. WhatsApp via Evolution API v2](#2a-whatsapp-via-evolution-api-v2)
3. [Instagram (mensagens e comentários)](#3-instagram-mensagens-e-comentários)
4. [Google Agenda](#4-google-agenda)
5. [Google Tarefas](#5-google-tarefas)
6. [Calendário do iCloud (CalDAV)](#6-calendário-do-icloud-caldav)
7. [iFood e 99Food](#7-ifood-e-99food)
8. [Diagnóstico de problemas](#8-diagnóstico-de-problemas)
9. [Checklist de segurança](#9-checklist-de-segurança)

---

## 0. Antes de tudo (vale para todas)

### 0.1 Quem pode configurar

Só os perfis **Administrador** e **Gestor** veem a tela *Configurações → Integrações*, conectam, trocam credenciais e enviam mensagens. Operador e Visualizador só enxergam a **Agenda**.

### 0.2 Endereço público do sistema (`BASE_URL`)

Webhooks e o retorno do login do Google precisam de um **endereço HTTPS público** que a Meta/Google consigam acessar. `localhost`, `127.0.0.1` ou IP de rede interna **não funcionam** para receber webhooks.

No arquivo `.env` do servidor:

```env
BASE_URL=https://sistema.suaempresa.com.br
```

- Sem barra no final, com `https://`.
- É esse valor que o sistema usa para montar a URL do webhook e a URL de retorno do Google. Se estiver errado (ex.: ainda `http://127.0.0.1:8000`), as URLs exibidas na tela ficam erradas e o provedor não consegue chamar o sistema.
- Para testar em desenvolvimento, use um túnel HTTPS (ex.: ngrok ou Cloudflare Tunnel) e coloque o endereço do túnel em `BASE_URL`.

### 0.3 Chave de criptografia das credenciais (`INTEGRACAO_CRYPTO_KEYS`) — **obrigatória em produção**

Todo token e senha digitados nas integrações são gravados **cifrados**. Em produção, **sem essa chave o sistema se recusa a salvar credenciais**.

1. Gere uma chave (no servidor, com o ambiente virtual ativo):

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Coloque no `.env`:

   ```env
   INTEGRACAO_CRYPTO_KEYS=chave_gerada_aqui
   ```

3. Reinicie o sistema.

**Cuidados:**
- **Guarde uma cópia da chave em local seguro** (cofre de senhas). Se ela se perder, todas as credenciais salvas ficam ilegíveis e será preciso reconectar tudo.
- **Nunca troque a chave simplesmente substituindo-a.** Para rotacionar: coloque a chave nova **na frente**, separada por vírgula (`nova,antiga`), rode `python manage.py rotacionar_credenciais` e só depois remova a antiga.
- Nunca coloque o `.env` no Git nem o envie por e-mail/chat.

### 0.4 Rotinas agendadas (cron / Agendador de Tarefas)

Algumas integrações dependem de comandos que precisam rodar periodicamente. Sem eles, o sistema "conecta" mas não envia/sincroniza.

| Comando | Para quê | Frequência sugerida |
|---|---|---|
| `python manage.py enviar_mensagens_pendentes` | envia a fila de mensagens (WhatsApp/Instagram) e refaz tentativas | a cada **1 min** |
| `python manage.py sincronizar_integracoes` | sincroniza Google Agenda, Google Tarefas e iCloud | a cada **15 min** |
| `python manage.py renovar_credenciais` | renova tokens do Google que estão para vencer | a cada **15–30 min** |
| `python manage.py enviar_lembretes_agenda` | lembretes de compromissos (sino/push) | a cada **5 min** |

No Linux use `cron`; no Windows, o **Agendador de Tarefas** (ação: `.venv\Scripts\python.exe manage.py <comando>`, na pasta do projeto). Detalhes de servidor em `README_ATUALIZAR_SERVIDOR.md`.

### 0.5 Fluxo geral na tela

*Configurações → Integrações* lista os provedores disponíveis em cartões.

- **Provedores com chaves manuais** (WhatsApp, Instagram, iCloud): clique no cartão, preencha o formulário (nome + credenciais + configuração) e salve. O sistema **testa a conexão na hora**: se o teste falhar, a conexão é criada com status "Com erro" e a mensagem do problema aparece. Corrija em *Alterar credenciais*.
- **Provedores Google** (Agenda, Tarefas): botão **Conectar com o Google** (login e autorização pelo próprio Google; você não digita senha nem chave no sistema).
- Na página da conexão há os botões **Testar**, **Sincronizar** (agenda), **Alterar credenciais**, **Desconectar** (revoga e apaga as credenciais) e **Excluir**, além do **log** dos últimos 50 eventos e, nas conexões de mensagem, a **URL do webhook**.
- Ao **editar credenciais**, campos secretos em branco **mantêm o valor atual** (o sistema nunca mostra segredos já salvos).
- Cada unidade (matriz/filial) tem suas próprias conexões. O nome da conexão deve ser único dentro da unidade.

---

## 1. WhatsApp Business — API oficial da Meta

**Para que serve no sistema:** enviar mensagens de texto (fila com reenvio automático) e **receber** mensagens por webhook (caixa de entrada + notificação à equipe). O sistema **não interpreta** o texto para criar pedidos sozinho: ele recebe a mensagem e avisa a equipe.

### O que você vai precisar obter (e onde cada um vai no sistema)

| Dado | Onde obter | Campo no sistema |
|---|---|---|
| **Phone Number ID** | Painel do app → WhatsApp → *API Setup* | **ID do número de telefone** |
| **Token de acesso permanente** | Usuário do sistema no Business Manager | **Token de acesso permanente** |
| **App Secret** | Painel do app → *App settings → Basic* | **Segredo do aplicativo (App Secret)** |
| **Token de verificação** | **Você inventa** (qualquer texto longo e aleatório) | **Token de verificação do webhook** |
| **ID da conta do WhatsApp Business (WABA)** — *opcional, só para os templates* | Business Manager → *Contas → Contas do WhatsApp* (o número do ID aparece ao selecionar a conta) ou painel do app → WhatsApp → *API Setup* | **ID da conta do WhatsApp Business (WABA)** |

### Passo a passo

**Passo 1 — Conta e app na Meta**
1. Tenha uma conta no **Meta Business** (business.facebook.com) — de preferência com a empresa **verificada** (Configurações do negócio → Central de segurança → Verificação).
2. Acesse **developers.facebook.com**, entre com o mesmo usuário e clique em **Meus Apps → Criar app**.
3. Escolha o caso de uso/tipo **Empresa (Business)** e vincule ao seu portfólio empresarial.
4. No painel do app, adicione o produto **WhatsApp** e siga o assistente (ele cria/associa uma *Conta do WhatsApp Business* — WABA).

**Passo 2 — Número de telefone**
1. Em **WhatsApp → API Setup** (Configuração da API) você verá um **número de teste** e o **Phone Number ID**. Copie o ID.
2. Para produção, adicione o **seu número real** ali mesmo ("Adicionar número de telefone") e confirme por SMS/ligação.
   - ⚠ O número **não pode estar em uso no aplicativo WhatsApp comum** (celular). Ao migrá-lo para a API, ele deixa de funcionar no app do celular.
   - Cada número tem seu próprio **Phone Number ID** — copie o do número que realmente vai usar.

**Passo 3 — Token permanente** *(o token que aparece em API Setup dura só ~24 h; não use esse em produção)*
1. No **Business Manager**: *Configurações do negócio → Usuários → Usuários do sistema → Adicionar*. Crie um usuário do sistema (função **Administrador**).
2. Clique em **Adicionar ativos** → selecione seu **app** com controle total (e a conta WhatsApp, se pedido).
3. Clique em **Gerar token**, escolha o app e marque as permissões **`whatsapp_business_messaging`** e **`whatsapp_business_management`**. Defina a expiração como **"Nunca"** (se disponível).
4. **Copie o token imediatamente** — a Meta só mostra uma vez.

**Passo 4 — App Secret**
1. No painel do app: **Configurações do app → Básico**.
2. Em **Chave secreta do app (App Secret)** clique em **Mostrar** (pede sua senha) e copie.

**Passo 5 — Criar a conexão no sistema**
1. *Integrações → WhatsApp Business (API oficial da Meta)*.
2. Preencha:
   - **Nome da conexão:** ex. `WhatsApp Loja Centro`
   - **Token de acesso permanente:** o token do passo 3
   - **Segredo do aplicativo:** o App Secret do passo 4
   - **Token de verificação do webhook:** invente um texto (ex.: gere com `python -c "import secrets; print(secrets.token_urlsafe(32))"`) e **anote** — você usará o mesmo na Meta
   - **ID do número de telefone:** o Phone Number ID do passo 2
3. Salve. O sistema chama a Meta para testar; deve aparecer "Conexão criada e verificada".

**Passo 6 — Configurar o webhook (para RECEBER mensagens)**
1. Abra a conexão no sistema e copie a **URL do webhook** (formato `https://seu-dominio/webhooks/<token-longo>`).
2. Na Meta: painel do app → **WhatsApp → Configuration (Configuração)** → *Webhook* → **Editar**.
3. **Callback URL:** cole a URL do webhook. **Verify token:** cole o mesmo texto do passo 5. Clique em **Verificar e salvar**.
   - A conexão precisa estar com status **Conectada** para a verificação passar (o sistema responde 404 caso contrário).
4. Em **Webhook fields**, clique em **Gerenciar** e **assine (Subscribe) o campo `messages`**.
5. Mande uma mensagem do seu celular para o número da empresa: ela deve aparecer em *Integrações → (conexão) → Mensagens → Recebidas* e a equipe é notificada.

**Passo 7 — Enviar**
Na página da conexão → **Mensagens**, informe o número do contato e o texto. A mensagem entra na fila e é enviada pelo comando `enviar_mensagens_pendentes` (item 0.4).

### Detalhes e cuidados específicos

- **Formato do número do contato:** somente dígitos, com código do país e DDD, sem `+`, espaços ou traços. Ex.: `5511999998888`.
- **Janela de 24 horas:** a Meta só permite enviar **texto livre** a quem **falou com a empresa nas últimas 24 h**. Fora da janela é obrigatório usar **modelos (templates) aprovados**. O sistema já permite **criar e gerenciar** os templates (seção abaixo), mas **ainda não envia mensagens usando um template** — fora da janela o envio falha com erro da Meta. Pense no WhatsApp aqui principalmente como **resposta a clientes que escreveram** e avisos dentro da janela.
- **Número de teste:** só envia para números cadastrados como destinatários de teste em *API Setup*. Para falar com clientes reais é preciso usar número próprio.
- **App em modo "Desenvolvimento":** a Meta só entrega webhooks reais depois que o app está em modo **Live (Publicado)**; no modo desenvolvimento só chegam eventos de teste. Publicar exige política de privacidade, ícone e categoria preenchidos.
- **Revisão da Meta:** para usar com clientes que não são administradores/testadores do app, a permissão `whatsapp_business_messaging` precisa de **acesso avançado** (App Review) e, em geral, da **verificação da empresa**.
- **Custos:** a Meta cobra por conversa/mensagem conforme a categoria e o país. Consulte a tabela de preços vigente antes de enviar em volume.
- **Limite do sistema:** 60 envios por minuto por conexão, até 5 tentativas com espera crescente; a mensagem tem no máximo 2.000 caracteres.
- **Proteção do webhook:** o sistema confere a assinatura `X-Hub-Signature-256` usando o App Secret. **Se o App Secret estiver errado, todas as mensagens recebidas são rejeitadas** (ficam no log como falha de assinatura). Se você regenerar o App Secret na Meta, atualize em *Alterar credenciais*.
- **Se o token for revogado ou o usuário do sistema excluído**, a conexão passa a dar erro; gere um novo token e atualize em *Alterar credenciais*.

### Templates de mensagem (criar, consultar, atualizar e excluir)

Disponível para conexões da API oficial que tenham o **ID da conta (WABA)** preenchido (*Alterar credenciais* → campo opcional; conexões antigas continuam funcionando sem ele, só os templates ficam indisponíveis).

1. *Integrações → Templates do WhatsApp* (ou o botão **Templates** na página da conexão).
2. **Novo template**: nome (minúsculas, números e sublinhado), idioma, categoria (**Marketing** ou **Utilidade**; autenticação/OTP não é criada pelo sistema), cabeçalho de texto, corpo, rodapé e até 3 botões na tela. Variáveis são `{{1}}`, `{{2}}`... (nunca no início/fim do corpo) e a Meta exige **um exemplo para cada variável**.
3. Salvar guarda um **rascunho só no sistema**. Use **Enviar à Meta** para submetê-lo à análise.
4. Há **dois estados lado a lado**: *Situação aqui* (rascunho / igual ao da Meta / alterado aqui) e *Status na Meta* (PENDING, APPROVED, REJECTED, PAUSED... exatamente como ela devolveu, com o motivo da rejeição quando houver). O sistema **nunca marca um template como aprovado sozinho**.
5. **Sincronizar com a Meta** (escolha a conexão no filtro) atualiza os status e importa os templates criados no painel da Meta. Templates com recursos que o sistema não edita (imagem/vídeo no cabeçalho, autenticação, carrossel...) entram como **somente leitura**.
6. **Editar** um template já enviado só é aceito pela Meta quando o status é APPROVED, REJECTED ou PAUSED; nome, idioma e categoria não mudam depois do envio (crie outro). Edições feitas aqui ficam como *alterado aqui* até você reenviar.
7. **Excluir** um template já enviado o apaga **também na Meta** (somente o idioma daquele registro). Só o Administrador tem essa permissão por padrão.

Cuidados: a análise da Meta pode demorar e rejeitar o texto; templates de marketing têm regras e custos próprios. A API REST equivalente está em `/api/v1/whatsapp-templates/` (Swagger, tag *integracoes*).

---

## 2. WhatsApp por provedor intermediário

**Quando usar:** quando você contrata um serviço terceiro (BSP/gateway de WhatsApp) em vez de usar a Meta diretamente. O sistema aceita qualquer provedor que ofereça **API HTTP** com o contrato abaixo.

> ⚠ Este conector é **genérico**: o provedor precisa falar o formato esperado pelo sistema. A maioria dos serviços comerciais tem endpoints e payloads próprios e **não funcionará sem um adaptador sob medida** (veja "Provedor novo" no [README.md](README.md)). Confirme com o provedor **antes** de contratar, ou peça a adaptação.

### Contrato esperado do provedor

| Ação | Chamada que o sistema faz |
|---|---|
| Testar | `GET {URL base}/status` com `Authorization: Bearer <token>` |
| Enviar | `POST {URL base}/mensagens` com corpo `{"para": "<número>", "texto": "<texto>"}`; resposta deve trazer `{"id": "..."}` |
| Receber | O provedor faz `POST` na URL do webhook com `{"id","de","texto","timestamp"}` (ou `{"mensagens":[...]}`) e o cabeçalho `X-Assinatura: sha256=<HMAC-SHA256 do corpo>` |

### Passo a passo

1. No painel do provedor, gere o **token de API** (geralmente em *Configurações → API / Desenvolvedores / Chaves*).
2. Defina (ou gere) um **segredo de webhook** para assinar as chamadas — o provedor usará esse segredo para calcular o HMAC-SHA256 do corpo.
3. No sistema: *Integrações → WhatsApp por provedor intermediário* e preencha:
   - **Nome da conexão**
   - **Token da API do provedor**
   - **Segredo do webhook (HMAC-SHA256)** — o mesmo definido no provedor
   - **URL base da API (https)** — ex.: `https://api.provedor.com.br/v1` (sem barra final)
4. Salve (o teste chama `/status`).
5. Copie a **URL do webhook** da página da conexão e cadastre no painel do provedor como destino de eventos de mensagem recebida.

### Cuidados

- A URL base **precisa ser HTTPS** e apontar para um endereço **público**. O sistema bloqueia endereços internos/privados (proteção contra SSRF).
- Se o provedor não assinar os webhooks com HMAC-SHA256 no cabeçalho `X-Assinatura`, **todas as mensagens recebidas serão recusadas** — por segurança o sistema nunca aceita webhook sem assinatura válida.
- As regras do WhatsApp (janela de 24 h, templates, número exclusivo, custos) valem também aqui, conforme o provedor.
- Desconfie de provedores "não oficiais" que usam o WhatsApp Web: há risco real de **banimento do número** pela Meta.

---

## 2A. WhatsApp via Evolution API v2

**Quando usar:** você (ou sua empresa) mantém um servidor da [Evolution API](https://doc.evolution-api.com) v2 e quer ligar uma **instância** dela ao sistema. É um conector próprio (`whatsapp_evolution`), à parte do "provedor intermediário" genérico da seção 2, que continua igual.

> ⚠ A Evolution usa o WhatsApp Web (não é a API oficial da Meta): há risco de **banimento do número**. Para volume ou uso crítico prefira a seção 1. Conector **não homologado** contra servidor real.

### O que você vai precisar

| Item | Onde obter | Onde colocar no sistema |
|---|---|---|
| URL base do servidor | endereço público **HTTPS** da sua Evolution (ex.: `https://evo.suaempresa.com.br`, sem barra final) | *URL base da Evolution API* |
| Nome da instância | criada no painel da Evolution (Manager) ou por `POST /instance/create` | *Nome da instância* (letras, números, `.`, `-`, `_`) |
| `apikey` | a chave da instância (ou a chave global `AUTHENTICATION_API_KEY` do servidor) | *Chave de API (apikey)* — fica cifrada |
| Segredo do webhook | **você inventa** um texto longo e aleatório | *Segredo do webhook* — fica cifrado |

### Passo a passo

1. Na Evolution, crie a instância e **leia o QR Code** com o WhatsApp do número (a instância precisa estar com estado `open`).
2. No sistema: *Integrações → WhatsApp via Evolution API (v2)*, preencha nome, URL base, instância, apikey e o segredo do webhook. Ao salvar, o sistema consulta `GET /instance/connectionState/{instância}`; se a instância não estiver `open`, a conexão fica "com erro" (corrija e use *Testar*).
3. Copie a **URL do webhook** da página da conexão.
4. Cadastre o webhook na Evolution (Manager → instância → Webhook, ou `POST {URL base}/webhook/set/{instância}` com o cabeçalho `apikey`), com o corpo abaixo — o cabeçalho `X-Webhook-Secret` precisa ter **exatamente** o segredo do passo 2:

```json
{
  "webhook": {
    "enabled": true,
    "url": "<URL do webhook copiada no passo 3>",
    "byEvents": false,
    "base64": false,
    "headers": { "X-Webhook-Secret": "<o mesmo segredo do passo 2>" },
    "events": ["MESSAGES_UPSERT"]
  }
}
```

### Como funciona e cuidados

- **Enviar:** `POST {URL base}/message/sendText/{instância}` com `{"number", "text"}` e cabeçalho `apikey`. Entra na mesma fila com reenvio das demais conexões.
- **Receber:** a Evolution **não assina** o corpo (sem HMAC). A autenticação é o cabeçalho `X-Webhook-Secret` (comparado em tempo constante) somada ao token imprevisível da URL. Sem o cabeçalho correto a chamada é recusada (403) e nada é gravado.
- Só entram mensagens **recebidas** (`fromMe = false`) de **conversas individuais** da **instância configurada**; grupos e eventos de outras instâncias são ignorados. Mensagens de mídia chegam com o texto vazio.
- A URL base precisa ser HTTPS e **pública** (endereços internos são bloqueados, proteção SSRF). Servidor só em rede interna/HTTP exige um proxy HTTPS público.
- A `apikey` e o segredo nunca aparecem em tela, log ou auditoria. Trocou a chave na Evolution? Use *Alterar credenciais*.

---

## 3. Instagram (mensagens e comentários)

**Escopo real:** receber mensagens diretas e comentários (caixa de entrada + notificação à equipe) e **responder mensagens diretas**. **Publicar posts/fotos NÃO está implementado.**

> ⚠ É a integração mais burocrática: exige **app da Meta aprovado em App Review** para as permissões de mensagens e comentários. Na data deste guia ainda não havia app aprovado — sem isso, só funciona com contas que sejam **administradoras/testadoras do app**.

### O que você vai precisar obter

| Dado | Onde obter | Campo no sistema |
|---|---|---|
| **ID da conta Instagram Business** | Graph API / *Graph API Explorer* (ver passo 3) | **ID da conta Instagram Business** |
| **Token de acesso** | Graph API Explorer / usuário do sistema | **Token de acesso da página/Instagram** |
| **App Secret** | Painel do app → *Configurações do app → Básico* | **Segredo do aplicativo** |
| **Token de verificação** | Você inventa | **Token de verificação do webhook** |

### Passo a passo

**Passo 1 — Pré-requisitos da conta**
1. A conta do Instagram precisa ser **Profissional (Comercial/Business)** — em *Instagram → Configurações → Tipo de conta e ferramentas → Mudar para conta profissional*.
2. Ela deve estar **vinculada a uma Página do Facebook** (Instagram → Configurações → Central de contas / Página vinculada).
3. Em *Instagram → Configurações → Mensagens e respostas de stories → Controles de mensagens → Ferramentas conectadas*, ative **"Permitir acesso às mensagens"**. Sem isso a API não vê as mensagens.

**Passo 2 — App na Meta**
1. Em developers.facebook.com, crie um app tipo **Empresa** (ou use o mesmo do WhatsApp).
2. Adicione o produto **Instagram** (API do Instagram com login do Facebook/Messenger API para Instagram, conforme o painel exibir).
3. Solicite as permissões **`instagram_manage_messages`** e **`instagram_manage_comments`** (e as de Página que o painel exigir, como `pages_show_list`, `pages_manage_metadata`). Para uso real com clientes, envie o app para **App Review** com vídeo demonstrando o uso.

**Passo 3 — Descobrir o ID da conta Instagram**
1. Abra o **Graph API Explorer** (developers.facebook.com/tools/explorer), escolha seu app e gere um token de usuário com as permissões acima.
2. Consulte `me/accounts` para obter o **ID da Página** e depois:
   `GET /{id-da-pagina}?fields=instagram_business_account`
3. O número em `instagram_business_account.id` é o que vai em **ID da conta Instagram Business**.

**Passo 4 — Token de longa duração**
1. No Graph API Explorer, gere o token de usuário com as permissões e troque por um de **longa duração** (60 dias) ou, melhor, use o **token da Página** derivado dele (não expira enquanto o token de usuário original for válido).
2. Alternativa mais estável: gerar o token por um **usuário do sistema** no Business Manager (igual ao passo 3 do WhatsApp), com as permissões do Instagram.
3. Copie o token.

**Passo 5 — Criar a conexão no sistema**
*Integrações → Instagram (mensagens e comentários)* e preencha nome, token, App Secret, token de verificação (invente um) e ID da conta Instagram Business. Salve; o sistema testa consultando a conta.

**Passo 6 — Webhook**
1. Copie a **URL do webhook** na página da conexão.
2. Painel do app → produto **Instagram → Configuração do webhook** (ou *Webhooks → Instagram*): cole a **Callback URL** e o **Verify token** e verifique.
3. Assine os campos **`messages`** e **`comments`**.

### Cuidados

- **Janela de resposta:** o Instagram só permite responder mensagens em até **24 h** após a última mensagem do usuário. O destino do envio é o **ID do usuário no Instagram** (não o @), que aparece como "contato" nas mensagens recebidas.
- **Tokens expiram:** token de usuário de longa duração vale ~60 dias e **este sistema não renova tokens da Meta automaticamente**. Prefira o token por usuário do sistema ou anote a data para trocar em *Alterar credenciais*.
- **Se o status virar "Com erro"** meses depois, o motivo mais comum é token expirado ou permissão revogada.
- Comentários recebidos aparecem com tipo "comentário"; o sistema **não responde comentários**, só mensagens diretas.
- Não vincule a conta a um app de terceiros que você não conhece: o token dá acesso às conversas dos clientes.

---

## 4. Google Agenda

**Para que serve:** sincroniza vencimentos de contas a pagar/receber, entregas, reservas e compromissos com o Google Agenda. Compromissos sincronizam **nos dois sentidos** (vence a alteração mais recente). A conexão é feita por **OAuth**: você não digita senha de usuário no sistema. Mas o **administrador do sistema** precisa criar uma vez as credenciais do aplicativo no Google Cloud (`GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET`).

### Parte A — Criar as credenciais no Google Cloud (uma vez só, vale para Agenda **e** Tarefas)

1. Acesse **console.cloud.google.com** com uma conta Google e crie um **projeto** (menu superior → *Novo projeto*), ex.: `SFC Integracoes`.
2. **Ative as APIs:** *APIs e serviços → Biblioteca* → pesquise e ative **Google Calendar API** (e **Google Tasks API**, se for usar Tarefas).
3. **Tela de consentimento OAuth:** *APIs e serviços → Tela de permissão OAuth* (ou *Google Auth Platform*).
   - Tipo de usuário: **Externo** (ou **Interno** se todos os usuários forem do mesmo Google Workspace da empresa).
   - Preencha nome do app, e-mail de suporte e e-mail do desenvolvedor.
   - Em **Escopos**, adicione `.../auth/calendar.events` (Agenda) e `.../auth/tasks` (Tarefas).
   - Em modo **Teste**, adicione na lista **Usuários de teste** o e-mail de cada conta Google que vai conectar.
4. **Criar o cliente OAuth:** *APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth*.
   - Tipo de aplicativo: **Aplicativo da Web**.
   - **URIs de redirecionamento autorizados:** adicione **exatamente**:

     ```
     https://sistema.suaempresa.com.br/integracoes/google/retorno
     ```

     (o mesmo endereço do seu `BASE_URL` + `/integracoes/google/retorno`; sem barra no final).
5. Copie o **ID do cliente** e a **Chave secreta do cliente**.

### Parte B — Colocar no sistema

No `.env` do servidor:

```env
GOOGLE_OAUTH_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
BASE_URL=https://sistema.suaempresa.com.br
```

Reinicie o sistema. Sem essas variáveis, o botão mostra a mensagem *"O Google não está configurado neste sistema"*.

### Parte C — Conectar (qualquer gestor)

1. *Integrações → Google Agenda → Conectar com o Google*.
2. Escolha a conta Google, leia e aceite a permissão (**"ver, editar, compartilhar e excluir permanentemente todos os calendários"**, é o texto que o Google usa para o escopo de eventos — o sistema só mexe nos eventos).
3. Se aparecer *"O Google não verificou este app"*, em modo Teste isso é esperado: clique em **Avançado → Acessar (nome do app)**.
4. Você volta ao sistema com a mensagem "Google Agenda conectado".
5. A conexão sempre nasce sincronizando o calendário `primary` (principal) da conta autorizada; a tela não oferece trocar o calendário. Se quiser separar, crie e use uma conta Google dedicada à empresa.

### Cuidados

- **A URL de redirecionamento tem que ser idêntica** (protocolo, domínio, caminho). Erro `redirect_uri_mismatch` = diferença de `http`/`https`, `www`, barra final ou `BASE_URL` incorreto.
- **Modo Teste expira o acesso em 7 dias:** com a tela de consentimento em "Teste", o Google invalida o *refresh token* em 7 dias e a conexão passa a dar erro. Para uso contínuo, **publique o app** ("Em produção"). Para escopos sensíveis em produção o Google pode exigir **verificação do app** (política de privacidade, domínio verificado, vídeo). Se a empresa usa Google Workspace, o tipo **Interno** evita essa verificação.
- **Valores em R$:** no formulário da conexão existe a opção **"Incluir valores em R$ nos títulos dos vencimentos"**. Deixe **desligada** se a agenda é compartilhada com pessoas que não devem ver valores financeiros — o dado vai para o Google.
- **Conexão revogada:** se o usuário remover o acesso em *myaccount.google.com → Segurança → Acesso de terceiros*, a conexão fica com erro; reconecte pelo botão.
- **Desativar um compromisso** no sistema remove o evento no Google. Excluir uma conta a pagar/receber também o remove.
- A sincronização considera uma **janela de 60 dias**. Eventos muito distantes não são importados.
- Use um e-mail **institucional** (não pessoal de um funcionário) como dono da conexão; se essa pessoa sair e a conta for desativada, a integração para.
- **Desconectar** no sistema revoga o acesso no Google e apaga as credenciais locais.

---

## 5. Google Tarefas

**Por que Tarefas e não Google Keep:** a API do Keep é voltada a administradores de Google Workspace e não permite editar notas. O Google Tarefas funciona com contas comuns e permite ler e escrever.

**O que sincroniza:** apenas os compromissos do tipo **Tarefa** da agenda (título, observações, data e **concluído**), nos dois sentidos. Vencimentos de contas e outros tipos **não** vão para o Google Tarefas.

### Passo a passo

1. Faça a **Parte A e B da seção 4** (o mesmo projeto, as mesmas variáveis `.env` e a mesma URL de retorno). Confirme que:
   - a **Google Tasks API** está **ativada** no projeto;
   - o escopo `https://www.googleapis.com/auth/tasks` está na tela de consentimento.
2. No sistema: *Integrações → Google Tarefas → Conectar com o Google*, escolha a conta e autorize.
3. Por padrão usa a lista principal (`@default`).
4. Crie na **Agenda** um compromisso do tipo **Tarefa** e clique em **Sincronizar** na conexão (ou aguarde a rotina de 15 min): ela aparece no app Google Tarefas.

### Cuidados

- **Erro 403** ("O Google recusou o acesso"): a Tasks API não está ativada no projeto ou o escopo `tasks` não está na tela de consentimento. Corrija no Google Cloud e **reconecte** a conta.
- **Só a data, sem hora:** o Google Tarefas guarda apenas o **dia** do prazo. A tarefa vira "dia inteiro" lá, e a hora definida no sistema é descartada no Google.
- **Tarefa sem data no Google é ignorada** (não há onde mostrá-la na agenda).
- **Conflito:** vence a alteração mais recente; empate, o sistema. Excluir no sistema remove do Google; excluir no Google desativa a tarefa local na próxima sincronização.
- O modo Teste do Google (7 dias) vale aqui também — veja a seção 4.
- Podem existir **duas conexões Google** (Agenda e Tarefas), cada uma pede sua autorização separadamente.

---

## 6. Calendário do iCloud (CalDAV)

**Importante:** o iCloud **não tem OAuth**. A integração usa o protocolo CalDAV com uma **senha específica de app** (nunca a senha do Apple ID).

### O que você vai precisar obter

| Dado | Campo no sistema |
|---|---|
| E-mail do Apple ID | **ID Apple (e-mail)** |
| Senha específica de app | **Senha específica de app** |
| Endereço CalDAV do calendário | **URL do calendário CalDAV (https)** |

### Passo a passo

**Passo 1 — Autenticação em dois fatores**
O Apple ID precisa ter a **autenticação de dois fatores** ativada (obrigatório para gerar senha de app).

**Passo 2 — Gerar a senha específica de app**
1. Acesse **account.apple.com** (ou appleid.apple.com) e entre.
2. Vá em **Login e segurança → Senhas específicas de app**.
3. Clique em **Gerar senha específica de app** (ou "+"), dê um nome como `SFC Sistema` e confirme.
4. **Copie a senha** (formato `xxxx-xxxx-xxxx-xxxx`). Ela só aparece uma vez.

**Passo 3 — Descobrir a URL do calendário**
A URL do CalDAV do iCloud é específica de cada conta (contém um número de usuário e um servidor `pXX-caldav.icloud.com`). Formas de obtê-la:
- Usar uma ferramenta de descoberta CalDAV (ex.: `caldav` em Python, ou apps como o *DAVx⁵*/*Cadaver*) apontando para `https://caldav.icloud.com` com o Apple ID + senha de app; ela lista os calendários e suas URLs.
- O formato final é parecido com: `https://p12-caldav.icloud.com/1234567890/calendars/<id-do-calendario>/`

Use a URL **do calendário específico** (não a raiz), com `https://`.

**Passo 4 — Criar a conexão**
*Integrações → Calendário do iCloud (CalDAV)*, preencha nome, ID Apple, senha de app e a URL, e salve. O teste faz uma consulta CalDAV ao calendário.

### Cuidados

- **Nunca use a senha real do Apple ID.** Se a senha de app vazar, revogue-a em account.apple.com (Senhas específicas de app) e gere outra — não é preciso trocar a senha principal.
- **Se você alterar a senha do Apple ID**, o Apple revoga todas as senhas de app: gere uma nova e atualize em *Alterar credenciais*.
- Use uma **conta Apple da empresa**, não a pessoal de um funcionário.
- Dê um **calendário dedicado** (ex.: "SFC") em vez do calendário pessoal principal, para evitar misturar compromissos particulares.
- O iCloud não tem notificação de mudança: as alterações feitas lá só chegam ao sistema na próxima sincronização (15 min).
- A senha de app e o ID Apple ficam cifrados; o ID Apple (e-mail) é tratado como credencial e também não é exibido depois.

---

## 7. iFood e 99Food

**Somente a estrutura existe.** Não há conector pronto: o sistema tem apenas o contrato para criar um adaptador (ver "Provedor novo (ex.: iFood, 99Food)" no [README.md](README.md)). Para ativar, será necessário: contrato/credenciais de parceiro do iFood ou 99Food, a documentação da API deles e o desenvolvimento do adaptador. Eles **não aparecem** na tela de integrações.

---

## 8. Diagnóstico de problemas

Sempre comece pela página da conexão: o campo **Último erro** e o **log** (últimos 50 eventos) dizem o que falhou — nunca contêm senhas ou tokens.

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "Conexão criada, mas o teste falhou" | Token/ID errado ou expirado | *Alterar credenciais*, confira cada campo e use **Testar** |
| Erro 401 / 190 / "token inválido" (Meta) | Token expirado ou revogado | Gere token novo (usuário do sistema) e atualize |
| Erro 403 (Google) | API desativada ou escopo ausente | Ative a API / adicione o escopo e **reconecte** |
| `redirect_uri_mismatch` | URL de retorno diferente da cadastrada | Ajuste no Google Cloud e confira `BASE_URL` |
| "O Google não está configurado…" | Faltam `GOOGLE_OAUTH_*` no `.env` | Preencher e reiniciar |
| Google para de sincronizar após ~7 dias | App Google em modo Teste | Publicar o app (seção 4) e reconectar |
| Verificação do webhook da Meta falha | Conexão não "Conectada", token de verificação diferente, `BASE_URL` errado ou sistema inacessível pela internet | Conferir os quatro pontos; testar a URL num navegador (deve dar 403, não timeout) |
| Mensagens recebidas não aparecem | App em modo Desenvolvimento, campo `messages` não assinado ou App Secret errado | Publicar o app, assinar o campo, corrigir o App Secret |
| Mensagens ficam "Na fila" | Rotina `enviar_mensagens_pendentes` não está agendada | Agendar a cada minuto (item 0.4) |
| Mensagem "Falhou" no WhatsApp/Instagram | Fora da janela de 24 h, número em formato errado ou destinatário de teste não cadastrado | Conferir formato (só dígitos com DDI) e a janela |
| "Configure INTEGRACAO_CRYPTO_KEYS…" | Falta a chave em produção | Seção 0.3 |
| "Não foi possível decifrar as credenciais" | `INTEGRACAO_CRYPTO_KEYS` foi trocada/perdida | Restaurar a chave antiga ou reconectar tudo |
| Erro 429 no webhook | Mais de 300 chamadas/min por conexão e IP | Normal em rajadas; verifique se não há laço de reenvio |
| URL recusada ("host não permitido") | Endereço http, interno ou privado | Use HTTPS com domínio público |

---

## 9. Checklist de segurança

- [ ] `INTEGRACAO_CRYPTO_KEYS` definida em produção e **copiada para um cofre**.
- [ ] `.env` fora do Git e com acesso restrito no servidor.
- [ ] `BASE_URL` correto, com HTTPS válido.
- [ ] Tokens gerados por **usuário do sistema / conta da empresa**, não por conta pessoal de funcionário.
- [ ] Permissões mínimas em cada provedor (só as listadas neste guia).
- [ ] Token/segredo vazado → **revogar no provedor primeiro**, depois atualizar no sistema.
- [ ] Opção "Incluir valores em R$" desligada em agendas compartilhadas.
- [ ] Rotinas agendadas (item 0.4) funcionando — confira o log da conexão após o primeiro dia.
- [ ] Apenas Administradores/Gestores com acesso às integrações.
- [ ] Testado com conta de homologação antes de ligar na operação; nenhuma integração anunciada como funcional sem esse teste.
- [ ] A **URL do webhook** é pública por natureza (o token nela não é segredo); a proteção real é a **assinatura**. Não "desligue" a verificação de assinatura nem compartilhe o App Secret.
- [ ] Mensagens e contatos são **dados pessoais (LGPD)**: tenha base legal/consentimento para contatar o cliente e uma política de retenção.
