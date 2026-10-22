# Sistema Faturando Certo (SFC)

Sistema web (e API para aplicativos mobile) de gestão e precificação para pequenas empresas: cadastro de insumos com custo por KG/litro/unidade, unidades de medida com conversões, usuários, perfis de acesso, auditoria completa e API documentada.

> **Como ler este documento:** ele foi escrito para quem está começando. Sempre que aparece um termo técnico (migration, ACL, token...), há uma explicação logo ao lado. Se você só quer **rodar o sistema**, leia de "Instalação" até "Execução". Se quer **mexer no código**, leia também "Estrutura de pastas" e "Manutenção".

## Sumário

1. [Visão geral](#visão-geral)
2. [Tecnologias](#tecnologias)
3. [Instalação](#instalação)
4. [Ambiente virtual](#ambiente-virtual)
5. [Dependências](#dependências)
6. [Variáveis de ambiente](#variáveis-de-ambiente)
7. [Banco de dados](#banco-de-dados)
8. [Migrations](#migrations)
9. [Usuário administrador](#usuário-administrador)
10. [Execução](#execução)
11. [Estrutura de pastas](#estrutura-de-pastas)
12. [Autenticação](#autenticação)
13. [ACL: Cliente, Perfil, Usuário e Permissão](#acl-cliente-perfil-usuário-e-permissão)
14. [API](#api)
15. [Swagger](#swagger)
16. [Auditoria](#auditoria)
17. [Insumos](#insumos)
18. [Módulos de negócio e como se conectam](#módulos-de-negócio-e-como-se-conectam)
19. [Medidas](#medidas)
20. [Regras de negócio](#regras-de-negócio)
21. [Máscaras](#máscaras)
22. [Indicadores](#indicadores)
23. [Encoding (UTF-8)](#encoding-utf-8)
24. [Segurança](#segurança)
25. [Testes](#testes)
26. [Manutenção](#manutenção)
27. [Decisões técnicas](#decisões-técnicas)
28. [Pendências e pontos de atenção](#pendências-e-pontos-de-atenção)

---

## Visão geral

O SFC permite que uma empresa (chamada de **Cliente**, identificada por CPF ou CNPJ) cadastre seus usuários e insumos e calcule o custo real de cada insumo. O sistema é **multi-tenant**: várias empresas usam o mesmo sistema, mas **cada uma só enxerga os próprios dados**.

Exemplo do dia a dia: você comprou 100 gramas de queijo por R$ 4,00. Ao cadastrar o insumo, o sistema calcula sozinho que **1 kg custa R$ 40,00** e mostra "100 gramas" por extenso abaixo do campo de quantidade.

O acesso é controlado por três camadas, todas verificadas no servidor:

| Camada | Pergunta que responde |
|---|---|
| **Cliente** | A qual empresa este usuário pertence? (isolamento dos dados) |
| **Módulo** | Esta empresa contratou/habilitou esta tela? |
| **Perfil + Permissões** | Este usuário pode visualizar, criar, editar ou excluir nesta tela? |

---

## Tecnologias

| Item | Tecnologia |
|---|---|
| Linguagem | Python 3.14 (funciona a partir do 3.12) |
| Framework | Django 5.2.5 |
| Banco de dados | SQLite (desenvolvimento/testes) e PostgreSQL (produção, ver [Banco de dados](#banco-de-dados)) |
| API | Django REST Framework 3.16 |
| Documentação da API | drf-spectacular (OpenAPI) + Swagger UI |
| Autenticação da API | Token `Bearer` com expiração |
| Frontend | Templates Django + tema Vuexy (Bootstrap), JavaScript simples (sem framework) |
| Exportações | openpyxl (Excel) e reportlab (PDF) |
| E-mail | Resend (opcional) |

---

## Instalação

Os comandos assumem **Windows com PowerShell**; no Linux/Mac, os equivalentes são indicados.

```bash
git clone <url-do-repositorio>
cd sfc-django
```

Pré-requisitos: Python 3.12+ e Git. Node.js é **opcional** (só se for recompilar o CSS/JS do tema; os arquivos prontos já estão em `src/assets`).

## Ambiente virtual

Um **ambiente virtual** ("venv") é uma pasta que isola as bibliotecas Python deste projeto do resto do computador. Crie e ative:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Linux/Mac: `python3 -m venv .venv && source .venv/bin/activate`

Quando o ambiente está ativo, o terminal mostra `(.venv)` no começo da linha.

## Dependências

Dependência = biblioteca de terceiros que o projeto usa. Instale todas de uma vez:

```bash
pip install -r requirements.txt
```

## Variáveis de ambiente

**Variável de ambiente** é uma configuração que fica fora do código (para não vazar senhas). No projeto, elas ficam no arquivo `.env` na raiz (ele **não** vai para o Git). Exemplo mínimo para desenvolvimento:

```
SECRET_KEY=uma-string-secreta-bem-grande-e-aleatoria
DEBUG=True
DJANGO_ENVIRONMENT=local
BASE_URL=http://127.0.0.1:8000
```

| Variável | Para que serve | Padrão |
|---|---|---|
| `SECRET_KEY` | Chave secreta do Django (assina sessões e tokens). **Obrigatória.** Em produção use um valor longo, aleatório e secreto | — |
| `DEBUG` | `True` mostra erros detalhados (**só em desenvolvimento**) | `False` |
| `DJANGO_ENVIRONMENT` | Nome do ambiente (`local`, `producao`...) | `local` |
| `BASE_URL` | Endereço público do sistema, usado nos links dos e-mails | — |
| `ALLOWED_HOSTS` | Domínios aceitos em produção, separados por vírgula | só localhost |
| `CSRF_TRUSTED_ORIGINS` | Origens HTTPS confiáveis (ex.: `https://app.exemplo.com`) | vazio |
| `TRUST_X_FORWARDED_FOR` | `True` **somente** se o sistema está atrás do nginx do projeto (lê o IP real do cliente) | `False` |
| `SECURE_SSL_REDIRECT` | Em produção, redireciona HTTP para HTTPS | `True` |
| `SECURE_HSTS_SECONDS` | Tempo (s) em que o navegador deve exigir HTTPS | `31536000` |
| `RESEND_API_KEY`, `MAIL_FROM_NAME`, `MAIL_FROM_EMAIL`, `MAIL_REPLY_TO` | Envio de e-mails (verificação de cadastro e redefinição de senha). Sem a chave, tudo funciona, menos o envio real | vazio |
| `LOGIN_RATE_LIMIT_ATTEMPTS` / `LOGIN_RATE_LIMIT_WINDOW` | Tentativas de login permitidas e janela em segundos | `3` / `900` |
| `PASSWORD_RESET_RATE_LIMIT_ATTEMPTS` / `_WINDOW` | Pedidos de redefinição de senha permitidos e janela | `2` / `3600` |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL` | Notificações push no navegador (gere com `python manage.py gerar_chaves_vapid`). Sem elas o push fica desligado; o sino da navbar funciona normalmente | vazio |
| `API_TOKEN_TTL_DIAS` | Validade do token da API | `30` |
| `API_THROTTLE_ANON`, `API_THROTTLE_USER`, `API_THROTTLE_CADASTRO`, `API_THROTTLE_SENHA` | Limites de requisições da API (`60/hour`, `2000/hour`, `10/hour`, `10/hour`) | veja ao lado |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Troca para PostgreSQL (ver abaixo) | SQLite |

**Nunca** faça commit de um `.env` com chaves reais.

## Banco de dados

**Desenvolvimento e testes:** SQLite — um arquivo `db.sqlite3` criado automaticamente, sem instalar nada.

**Produção (PostgreSQL):**

1. Crie o banco **em UTF-8** (importante para acentos):
   ```sql
   CREATE DATABASE sfc ENCODING 'UTF8' LC_COLLATE 'pt_BR.UTF-8' LC_CTYPE 'pt_BR.UTF-8' TEMPLATE template0;
   ```
2. Instale o driver: `pip install "psycopg[binary]"`
3. No `.env`: `DB_ENGINE=postgres`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.
4. Rode `python manage.py migrate`.

> As migrations foram escritas para funcionar nos dois bancos (por exemplo, a migração dos insumos separa alteração de estrutura e de dados). A suíte de testes foi executada em SQLite; rode-a também no PostgreSQL antes de ir para produção.

## Migrations

**Migration** é o mecanismo do Django que registra, em arquivos versionados, cada alteração na estrutura do banco (criar tabela, adicionar coluna...). Assim todos os ambientes ficam idênticos.

```bash
python manage.py makemigrations   # cria migrations a partir das mudanças nos models (só quem desenvolve)
python manage.py migrate          # aplica as migrations pendentes no banco
```

Depois do primeiro `migrate`, rode também os dados padrão (são **idempotentes**: pode rodar quantas vezes quiser sem duplicar):

```bash
python manage.py seed_acl        # cria os perfis padrão e suas permissões
python manage.py seed_modulos    # atualiza o catálogo de módulos, os segmentos padrão e as habilitações
python manage.py gerar_chaves_vapid   # (opcional) chaves das notificações push; cole no .env
```

A tabela de **medidas** já nasce preenchida (17 unidades) pelo próprio `migrate`.

## Usuário administrador

```bash
python manage.py createsuperuser
```

O superusuário acessa tudo, inclusive `/admin/`. Para o uso normal do sistema, o ideal é um usuário no perfil **Administrador** (veja [ACL](#acl-cliente-perfil-usuário-e-permissão)).

Primeiro acesso (uma vez só): crie o primeiro Cliente e vincule seu usuário a ele, no `python manage.py shell`:

```python
from apps.cliente.models import Cliente
from django.contrib.auth.models import User

cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Minha Empresa")
usuario = User.objects.get(username="SEU_USUARIO")
usuario.profile.cliente = cliente
usuario.profile.save()
```

Depois, em **Módulos**, habilite os módulos que a empresa vai usar. Daí em diante, novos clientes e usuários são criados pelas telas **Clientes** e **Usuários**.

## Execução

```bash
python manage.py runserver
```

Acesse http://127.0.0.1:8000/ (vai para o login). Documentação da API: http://127.0.0.1:8000/api/docs/ (somente administradores logados).

Produção: o projeto traz `Dockerfile`, `docker-compose.yml`, `gunicorn-cfg.py` e `nginx/`. Com `DEBUG=False` o sistema liga automaticamente as proteções HTTPS (ver [Segurança](#segurança)).

---

## Estrutura de pastas

```
sfc-django/
├── apps/                       # Cada subpasta é um "app" Django = um módulo do sistema
│   ├── acl/                    # Telas de Perfis (papéis) e Permissões + services.py
│   ├── api/                    # API REST v1: views, serializers, permissões, erros, Swagger
│   ├── auditoria/              # Log de auditoria: utils.py (registrar), mixins.py, telas
│   ├── canal/                  # Canais de venda e suas taxas (Configurações)
│   ├── dashboard/              # Painel (página inicial) e usuários online
│   ├── financeiro/             # Contas a pagar/receber, baixas, DRE e balanço gerenciais
│   ├── estoque/                # Estoque: lotes, validade, movimentações, transferências e alertas
│   ├── custo/                  # Custo operacional fixo/variável distribuído por categoria
│   ├── categoria/              # Categorias (usadas pelos produtos) com ticket médio e % do faturamento
│   ├── compras/                # Lista de compras: sugestões do estoque, itens manuais e serviços
│   ├── pdv/                    # PDV: caixa, vendas, comandas de mesa, devolução e comprovante não fiscal
│   ├── pedido/                 # Pedidos e cozinha: fila de produção, confirmação com baixa de estoque, eventos
│   ├── maodeobra/              # Mão de obra por hora/dia: colaboradores, apontamentos, hora extra e relatório (serviço)
│   ├── integracao/             # Integrações (WhatsApp, Instagram, Google Agenda, iCloud), mensagens, webhooks e agenda
│   ├── orcamento/              # Orçamentos de produtos/serviços: PDF, e-mail, WhatsApp e link público
│   ├── fiscal/                 # Fiscal: cadastro, certificado A1 cifrado, pedidos de documento e fila (SEM emissão real ainda)
│   ├── pdvconfig/              # Configurações do PDV: formas de pagamento (taxa), mesas e ajustes
│   ├── cotacao/                # Cotações entre lojas: comparativo, vencedores, conta a pagar, PDF/Excel
│   ├── consumidor/             # Consumidores (clientes finais): cadastro, LGPD, ViaCEP/BrasilAPI
│   ├── cliente/                # Clientes (tenants), Matriz/Filiais, segmento e Gestor padrão
│   ├── insumo/                 # Insumos (cálculo de preço por KG/L/UN)
│   ├── medida/                 # Unidades de medida (tabela `medidas`) + prévia de cálculo
│   ├── modulo/                 # Catálogo de módulos (menu/permissões), Segmentos e habilitação por cliente
│   ├── notificacao/            # Notificações (Web Push, pronto para FCM), histórico e caixa de entrada
│   ├── tour/                   # Tours de página reutilizáveis (initTour) e "não mostrar novamente"
│   ├── usuario/                # Cadastro de usuários (cliente + perfil)
│   ├── processado/             # Receitas e processados + motor de recálculo em cascata
│   ├── produto/, ficha/        # Produtos e Fichas técnicas (itens, custo, exportação)
│   ├── precificacao/           # Precificação: preço de venda, lucro e CMV (margem/markup/valor)
│   └── pages/, sample/         # Páginas de sistema e exemplos do tema
├── auth/                       # Login, cadastro, senha, perfil; services.py compartilhado com a API
├── config/                     # settings.py, urls.py
├── web_project/
│   ├── regras/                 # REGRAS DE NEGÓCIO globais (dinheiro, conversão, extenso, indicadores...)
│   ├── decorators.py           # screen_required / module_required (proteção de telas)
│   ├── mixins.py               # ClienteScopedMixin (isolamento por cliente)
│   ├── acesso.py               # REGRAS CENTRAIS de escopo: clientes/usuários/perfis/módulos acessíveis
│   ├── menu.py                 # Menu lateral montado a partir da tabela de módulos
│   └── forms.py                # Campos de formulário reutilizáveis (DecimalBRField, MoedaCentavosField)
├── src/assets/js/              # mascaras.js, calculo-medida.js, template-customizer-pt-br.js
├── templates/                  # Layout, navbar, itens estáticos do menu, telas de auth
├── nginx/, Dockerfile, docker-compose.yml, gunicorn-cfg.py
└── manage.py
```

---

## Autenticação

**Autenticação** = provar quem você é.

- **Web:** login com usuário **ou** e-mail e senha (`/login/`). A sessão é guardada no banco e o cookie é `HttpOnly`, `SameSite=Lax` e `Secure` (`SESSION_COOKIE_SECURE`, padrão `True`; em HTTP puro de desenvolvimento defina `SESSION_COOKIE_SECURE=False` no `.env`). O login tem **rate limit**: após 3 falhas seguidas, bloqueia por 15 minutos. O destino `next` só é aceito se for do próprio sistema (sem redirecionamento aberto).
- **"Lembrar-me"** (`auth/login/views.py`): marcado, a sessão dura **30 dias** (`SESSION_REMEMBER_AGE`, em segundos, configurável); desmarcado, o cookie some ao **fechar o navegador** e a sessão expira após **1 hora de inatividade** (`SESSION_COOKIE_AGE`). O prazo é **deslizante** (`SESSION_SAVE_EVERY_REQUEST`): cada acesso renova. Nada além do cookie de sessão fica no navegador (nunca senha nem token). **Revogação**: sair (logout) apaga a sessão; trocar a senha invalida todas as sessões antigas (o hash de autenticação da sessão deixa de conferir); desativar a conta derruba as sessões; cada dispositivo tem a sua sessão e sair de um não derruba os outros. Mensagens de erro continuam genéricas ("Nome de usuário ou senha inválidos."), sem revelar se a conta existe. A tela de login **não exibe regras de tamanho** de usuário/senha (só "Informe..."); a regra da senha aparece apenas no cadastro e na redefinição, e o servidor valida sempre.
- **Cadastro:** `/register/` cria a conta no perfil de menor privilégio (Visualizador), **sem cliente** (ou seja, sem acesso a nenhum módulo) e envia e-mail de verificação. Um administrador vincula o cliente e o perfil depois.
- **Senhas:** nunca são guardadas em texto puro (hash PBKDF2 nativo do Django) e passam pelos validadores do Django (tamanho mínimo, não ser comum, não ser só números, não parecer com o usuário) no cadastro, na redefinição e na API.
- **Recuperação de senha:** "Esqueci minha senha" envia um link com token de **uso único** válido por 24 horas. A resposta é igual exista ou não o e-mail (não revela quais contas existem).
- **API:** token `Bearer` (veja [API](#api)).

A lógica de cadastro/redefinição fica em `auth/services.py` e é **a mesma** para web e API.

## ACL: Cliente, Perfil, Usuário e Permissão

**ACL** (Access Control List) é a lista de quem pode fazer o quê. A cadeia do sistema é:

```
Usuário → Cliente → Segmento de Trabalho → Módulos habilitados → Permissões do Perfil → Dados
```

```
Cliente
 ├── Matriz ── Filial, Filial...      (estrutura: todos enxergam só a própria)
 └── Segmento ── Módulos              (o segmento pertence ao CLIENTE, não ao usuário)
```

| Conceito | O que é | Onde |
|---|---|---|
| **Usuário** | Pessoa que loga (`auth_user` do Django + `Profile`) | tela **Usuários** |
| **Cliente** | Empresa (CPF/CNPJ), **Matriz** ou **Filial** de uma matriz. Isola os dados: ninguém vê dados de outra estrutura | tela **Clientes** |
| **Segmento** | Define o conjunto de módulos do cliente (ex.: Produto, Serviço) | tela **Segmentos** |
| **Módulo** | Tela do sistema: nome, ícone, rota, ordem e permissão (vira item de menu e linha na matriz de papéis) | tela **Módulos** |
| **Perfil** | Papel (`Group` do Django): Administrador, Gestor, Operador, Visualizador ou customizado | tela **Papéis** |
| **Permissão** | Ação em um módulo (`insumo.view_insumo`, `insumo.add_insumo`...) | tela **Permissões** (catálogo) |

Regras importantes:

- Cada **Cliente** tem uma lista de **perfis disponíveis** (tela Clientes). Ao criar um usuário, o perfil escolhido **precisa estar nessa lista** (validado no servidor).
- Quem **não** é administrador global (superusuário ou perfil "Administrador", o **Admin**) só enxerga clientes, usuários, notificações e auditoria da **própria estrutura** (seu cliente, a matriz e as filiais dela); insumos e demais dados de negócio são do **cliente exato**. Isso é filtrado no backend (`web_project/acesso.py`): trocar o `id` na URL devolve 404.
- Para um módulo ser usado, ele precisa estar **no segmento do cliente**, **habilitado para o cliente** e **permitido ao perfil**. Módulos de Configurações são *essenciais*: só dependem do perfil.
- Ao criar um cliente, o sistema cria **automaticamente** o usuário **Gestor** (mesmo nome do cliente; a senha é definida por "Esqueci minha senha"), numa transação.
- O perfil **Admin** fica oculto para quem não é Admin, só pode ser vinculado ao cliente **SISTEMA FATURANDO CERTO SERVIÇOS LTDA** (`Cliente.sistema`) e só um Admin o administra. Gestor e Operador ficam sempre visíveis.
- Perfis padrão (criados por `seed_acl`): **Administrador** (tudo, inclusive Clientes, Módulos, Medidas, Usuários, Auditoria, Papéis), **Gestor** (ver/criar/editar), **Operador** (ver/criar), **Visualizador** (só ver).
- Permissões sensíveis (gerenciar papéis/usuários) **não** podem ser atribuídas pela tela de Papéis — só o grupo Administrador as tem.
- Usuários **nunca são excluídos**, só inativados (preserva histórico e auditoria).

Detalhes técnicos do desenho original em `Group-ACL.md`. Cada app tem um `README.md` próprio (objetivo, tabelas, fluxo e manutenção): comece por `web_project/README.md`, `apps/cliente/README.md`, `apps/modulo/README.md` e `apps/notificacao/README.md`.

### Notificações e tours

- **Notificações** (`apps/notificacao`): envio para todos, por segmento ou usuários específicos (com tela de confirmação), histórico com filtros/paginação e o sino da navbar com contador, rolagem quando há mais de 3 itens e "Ver todas as notificações". Notificações **lidas são removidas 48 h depois de lidas** (comando `limpar_notificacoes_lidas`). A entrega é feita por *canais* (`canais.py`): hoje Web Push; o FCM (mobile) entra como um novo canal.
- **Tours** (`apps/tour`): `initTour({page, steps})` em qualquer tela; "Não mostrar novamente" é salvo por usuário.

---

## API

API REST para o aplicativo mobile. Base: `/api/v1/`. Formato **sempre JSON**.

### Como usar (passo a passo)

1. `POST /api/v1/auth/login/` com `{"login": "usuario ou e-mail", "password": "..."}` → recebe `token`.
2. Envie o token em todas as chamadas: cabeçalho `Authorization: Bearer <token>`.
3. O token vale 30 dias (`API_TOKEN_TTL_DIAS`), cada login invalida o token anterior e o logout o apaga.

### Formato das respostas

Sucesso:
```json
{ "success": true, "message": "Registro criado com sucesso.", "data": { } }
```
Erro:
```json
{ "success": false, "message": "Requisição inválida.", "errors": { "campo": ["mensagem"] } }
```
Listas vêm em `data = {"itens": [...], "paginacao": {"pagina", "por_pagina", "total", "total_paginas"}}` (`?pagina=2&por_pagina=50`, máximo 100).

| Código | Quando |
|---|---|
| 200 / 201 / 204 | Sucesso / criado / sucesso sem corpo (ex.: DELETE, logout) |
| 400 | Dados inválidos (detalhes em `errors`) |
| 401 | Sem token, token inválido/expirado, usuário ou senha incorretos |
| 403 | Sem permissão, módulo não habilitado ou sem cliente |
| 404 | Não existe (ou é de outro cliente) |
| 405 | Método não existe no recurso (ex.: não há DELETE de usuário) |
| 409 | Duplicidade (nome/e-mail/CPF já cadastrado) |
| 415 | Corpo não é JSON |
| 422 | Viola regra de negócio (ex.: alterar o fator de uma medida em uso) |
| 429 | Muitas requisições / tentativas de login |
| 500 | Erro interno (a mensagem nunca revela detalhes internos) |

### Dinheiro na API

**Nunca é float.** Todo valor monetário vem como inteiro em **centavos**, mais uma versão em texto decimal e outra formatada:

```json
{ "preco_pago": 400, "preco_pago_decimal": "4.00", "preco_pago_formatado": "R$ 4,00" }
```
Para **enviar**, use somente o inteiro em centavos (`"preco_pago": 400`). Quantidades vêm como texto decimal (`"100.0000"`).

### Endpoints

Permissão = permissão nativa do Django exigida (além de estar autenticado). "Módulo" = o módulo precisa estar habilitado para o cliente do usuário. Todos, exceto os marcados como públicos, exigem `Authorization: Bearer`.

| Método | Endpoint | Descrição | Autenticação | Permissão |
|---|---|---|---|---|
| POST | `/auth/login/` | Login (usuário ou e-mail); devolve token e dados do usuário (cliente, permissões, módulos) | pública (rate limit) | — |
| POST | `/auth/logout/` | Invalida o token | Bearer | — |
| GET | `/auth/me/` | Dados do usuário logado | Bearer | — |
| POST | `/auth/register/` | Autocadastro (sem cliente, perfil Visualizador, e-mail de verificação) | pública (throttle) | — |
| POST | `/auth/password/change/` | Altera a própria senha; devolve novo token | Bearer | — |
| POST | `/auth/password/forgot/` | Pede e-mail de redefinição (resposta sempre igual) | pública (throttle) | — |
| POST | `/auth/password/reset/` | Redefine a senha com o token do e-mail | pública (throttle) | — |
| GET/POST | `/insumos/` | Lista (filtros `q`, `ativo`, `medida`, `ordenar`) / cria | Bearer + módulo `insumo` | `insumo.view_insumo` / `add_insumo` |
| GET/PUT/PATCH/DELETE | `/insumos/{id}/` | Consulta / atualiza / atualiza parcial / inativa (exclusão lógica, 204) | Bearer + módulo | `view` / `change` / `change` / `delete` |
| GET/POST | `/categorias/` | Lista / cria | Bearer + módulo `categoria` | `categoria.view_categoria` / `add_categoria` |
| GET/PUT/PATCH | `/categorias/{id}/` | Consulta / atualiza | Bearer + módulo | `view` / `change` |
| GET | `/medidas/`, `/medidas/{id}/` | Lista/consulta (filtros `q`, `dimensao`, `ativo`) | Bearer | qualquer usuário autenticado |
| POST | `/medidas/` | Cria | Bearer | `medida.add_medida` |
| PUT/PATCH | `/medidas/{id}/` | Atualiza | Bearer | `medida.change_medida` |
| DELETE | `/medidas/{id}/` | Exclui; se estiver em uso, apenas inativa (200) | Bearer | `medida.delete_medida` |
| POST | `/medidas/{id}/ativar/`, `/inativar/` | Muda o status | Bearer | `medida.change_medida` |
| GET/POST | `/usuarios/` | Lista (filtros `q`, `cliente`, `perfil`, `ativo`) / cria | Bearer | `auth.view_user` / `add_user` |
| GET/PUT/PATCH | `/usuarios/{id}/` | Consulta / atualiza | Bearer | `view_user` / `change_user` |
| POST | `/usuarios/{id}/ativar/`, `/inativar/` | Muda o status (não inativa a si mesmo) | Bearer | `auth.change_user` |
| GET/POST | `/clientes/` | Lista / cria (CPF/CNPJ validado) | Bearer | `cliente.view_cliente` / `add_cliente` |
| GET/PUT/PATCH | `/clientes/{id}/` | Consulta / atualiza (inclui `perfis` disponíveis) | Bearer | `view` / `change` |
| POST | `/clientes/{id}/ativar/`, `/inativar/` | Muda o status | Bearer | `cliente.change_cliente` |
| GET/PUT | `/clientes/{id}/modulos/` | Lista / define os módulos habilitados do cliente | Bearer | `cliente.change_cliente` |
| GET | `/modulos/`, `/modulos/{id}/` | Catálogo de módulos | Bearer | `modulo.view_modulo` |
| GET/POST | `/perfis/` | Lista / cria perfis (papéis) com permissões | Bearer | `auth.view_group` / `add_group` |
| GET/PUT/PATCH/DELETE | `/perfis/{id}/` | Consulta / atualiza / exclui | Bearer | `view/change/delete_group` |
| GET | `/permissoes/` | Catálogo de permissões atribuíveis | Bearer | `auth.view_permission` |
| GET | `/auditoria/`, `/auditoria/{id}/` | Trilha de auditoria (filtros `q`, `usuario`, `modulo`, `acao`, `de`, `ate`); somente leitura | Bearer | `auditoria.view_registroauditoria` |
| GET/POST/PUT/PATCH/DELETE | `/formas-pagamento/`, `/mesas/` (+ `/{id}/`) | Formas de pagamento com taxa (`taxa_modo`: sem_taxa/percentual/fixa/ambas) e mesas do PDV | Bearer + módulo | `pdvconfig.*_formapagamento` / `*_mesa` |
| GET/PATCH | `/pdv/configuracao/`, `/pdv/produtos/` | Ajustes do PDV da unidade; produtos vendáveis com o preço do PDV | Bearer + módulo | `pdvconfig.*_configuracaopdv` / `pdv.add_venda` |
| GET/POST | `/pdv/caixa/…` (`atual`, `abrir`, `fechar`, `sangria`, `suprimento`, `{id}/conferir`) | Caixa do operador, histórico e conferência | Bearer + módulo `pdv` | `pdv.view_caixa`, `abrir_caixa`, `sangria`, `suprimento`, `conferir_caixa` |
| GET/POST | `/pdv/vendas/`, `/pdv/vendas/{id}/`, `…/cancelar/`, `…/itens/{item}/devolver/`, `…/comprovante/` | Vendas de balcão **idempotentes** (`chave`/`Idempotency-Key`), cancelamento, devolução e comprovante (json/pdf/escpos) | Bearer + módulo | `view_venda`, `add_venda`, `cancelar_venda`, `devolver_item` |
| GET/POST/DELETE | `/pdv/comandas/…` (`itens/`, `pagar/`) | Comandas de mesa, lançamento de itens e recebimento (conta dividida) | Bearer + módulo | `view_venda` / `add_venda` |
| GET/POST | `/pedidos/` (+ `fila/`, `{id}/confirmar|avancar|voltar|cancelar/`) | Pedidos de produção **idempotentes** (`chave`/`Idempotency-Key`), fila da cozinha e mudança de estado | Bearer + módulo `pedido` | `pedido.view_pedido`, `add_pedido`, `mover_pedido`, `cancelar_pedido` |
| GET/POST/PUT/PATCH/DELETE | `/colaboradores/`, `/apontamentos/` (+ `/{id}/`) | Mão de obra por hora/dia (cálculo no servidor, snapshot); só segmentos de serviço | Bearer + módulo `mao-de-obra` | `maodeobra.*_colaborador` / `*_apontamento` (Gestor e Administrador) |
| GET/PATCH | `/mao-de-obra/regras/`, GET `/mao-de-obra/relatorio/` | Arredondamento, jornada, hora extra e domingo; relatório por colaborador | Bearer + módulo | `maodeobra.change_configuracaomaodeobra` / `view_apontamento` |
| GET/POST/PATCH | `/integracoes/` (+ `provedores/`, `{id}/testar|sincronizar|desconectar|credenciais/`) | Conexões com provedores (credenciais cifradas, **nunca devolvidas**) | Bearer + módulo `integracoes` | `integracao.view_conexao`, `add_conexao`, `change_conexao` (Gestor/Administrador) |
| GET/POST | `/mensagens/` | Fila de mensagens (POST **idempotente**; envio assíncrono, sem garantia de entrega instantânea) | Bearer + módulo | `integracao.view_mensagem`, `enviar_mensagem` |
| GET/POST/PUT/PATCH/DELETE | `/compromissos/` (+ `/{id}/`) | Agenda de compromissos, reservas e entregas (filtros `situacao=aberto\|atrasado\|concluido`, `tipo`, `inicio_de`, `inicio_ate`, `q`, `ativo`) | Bearer + módulo `agenda` | `integracao.*_compromisso` |
| GET | `/fiscal/configuracao/` | Configuração e pendências fiscais da unidade (sem segredos; certificado não trafega pela API) | Bearer + módulo `fiscal-config` | `fiscal.view_configuracaofiscal` |
| GET/POST | `/fiscal/documentos/` (+ `{id}/`, `{id}/cancelar/`) | Pedido de documento fiscal por venda paga (**idempotente**, fica PENDENTE com o motivo) | Bearer + módulo `fiscal` | `fiscal.view_documentofiscal`, `fiscal.emitir_nota` |
| GET/POST | `/cotacoes/`, `/cotacoes/{id}/` | Cotações (detalhe com lojas, itens, ofertas avaliadas e vencedores); DELETE recusado se houver conta gerada | Bearer + módulo `cotacao` | `cotacao.*_cotacao` |
| GET/POST/PATCH/DELETE | `/cotacoes/{id}/itens/`, `lojas/`, `precos/` (+ `/{sub_id}/`) | Itens, lojas e preços (dinheiro em centavos); só em cotação rascunho/enviada (422 nas demais) | Bearer + módulo | `view_` (GET) / `change_cotacao` |
| POST | `/cotacoes/{id}/importar-lista/`, `escolher/`, `limpar-escolha/`, `status/`, `gerar-contas/` | Copia a lista de compras (idempotente); define o vencedor; envia/fecha/reabre/cancela; gera conta a pagar por loja vencedora (idempotente) | Bearer + módulo | `change_cotacao` (+ `financeiro.add_contapagar` em `gerar-contas`) |
| GET | `/cotacoes/{id}/comparativo/`, `/cotacoes/{id}/exportar/?formato=pdf\|xlsx` | Ofertas avaliadas (atacado, total, preço por KG/L/UN, menor preço) e exportação | Bearer + módulo | `view_cotacao` |
| GET/POST | `/compras/itens/`, `/compras/itens/{id}/` | Lista de compras: itens de insumo ou avulsos/serviços (CRUD auditado) | Bearer + módulo `compras` | `compras.*_itemcompra` |
| GET / POST | `/compras/sugestoes/`, `/compras/sugestoes/adicionar/` | Sugestões do estoque `(mínimo × fator) − saldo` (múltiplo de compra opcional); adicionar é idempotente e a quantidade é do servidor | Bearer + módulo | `view_` / `add_itemcompra` |
| GET | `/estoque/saldos/{insumo}/etiqueta/` | PDF de etiquetas com QR (`formato=a4\|termica`, `copias`, `lote`, `emissao`, `modo_validade=digitada\|calculada`, `validade`) | Bearer + módulo `estoque` | `estoque.view_movimentoestoque` |
| GET/POST | `/consumidores/`, `/consumidores/{id}/` | Consumidores (clientes finais): lista com documento mascarado e sem contatos; detalhe completo; `DELETE` **anonimiza** (LGPD) | Bearer + módulo `consumidor` | `consumidor.*_consumidor` |
| GET | `/consumidores/consulta-cep/{cep}/`, `/consumidores/consulta-cnpj/{cnpj}/` | Sugestão de endereço (ViaCEP) e de dados do CNPJ (BrasilAPI); não grava; 30/min por usuário | Bearer + módulo | `consumidor.add_consumidor` |
| GET/POST | `/produtos/`, `/produtos/{id}/` | Produtos: lista (`q`, `categoria`, `ativo`, `ordenar`) / cria; consulta / atualiza (PUT, PATCH) / exclui (sem uso apaga; em uso só desativa) | Bearer + módulo `produto` | `produto.view/add/change/delete_produto` |
| GET/POST | `/canais-venda/`, `/canais-venda/{id}/` | Canais de venda com `taxas` (percentual OU valor fixo em centavos); alterar taxas refaz as precificações | Bearer + módulo `canal-venda` | `canal.*_canalvenda` |
| GET/POST | `/custos-operacionais/`, `/custos-operacionais/{id}/` | Custos fixos/variáveis mensais (`valor` em centavos; `modelo` = custo comum) | Bearer + módulo `custo-operacional` | `custo.*_custooperacional` |
| GET/POST | `/processados/`, `/processados/{id}/` | Processados/receitas com `itens` (insumo, medida, quantidade); custo e insumo gerado calculados; DELETE lógico | Bearer + módulo `processado` | `processado.*_processado` |
| GET/POST | `/fichas-tecnicas/`, `/fichas-tecnicas/{id}/` | Fichas técnicas (nome vem do produto) com `itens`; custo calculado | Bearer + módulo `ficha` | `ficha.*_ficha` |
| GET | `/processados/{id}/exportar/`, `/fichas-tecnicas/{id}/exportar/` | Arquivo individual: `formato=pdf\|xlsx`, `incluir_receita=true\|false`. `receita`/`observacoes` (também nos GET/POST/PUT dessas rotas) só existem para quem tem a permissão `ver_receita` | Bearer + módulo | `view_*` (+ `ver_receita` para o texto) |
| GET/POST | `/precificacoes/`, `/precificacoes/{id}/` | Precificações calculadas pelo servidor; `custo_operacional` omitido = automático (distribuição), outro valor = manual | Bearer + módulo `precificacao` | `precificacao.*_precificacao` |
| GET | `/estoque/saldos/`, `/estoque/saldos/{insumo}/` | Saldos por insumo (filtros `q`, `situacao`, `ordenar`) e detalhe com lotes | Bearer + módulo `estoque` | `estoque.view_movimentoestoque` |
| PUT | `/estoque/saldos/{insumo}/minimo/` | Define o estoque mínimo (`{"minimo": "5"}`) | Bearer + módulo | `estoque.change_movimentoestoque` |
| GET | `/estoque/alertas/` | Estoque baixo e lotes a vencer | Bearer + módulo | `estoque.view_movimentoestoque` |
| POST | `/estoque/movimentos/` | Registra entrada, saída ou ajuste (validade pode vir sem lote; saída segue o PEPS; **`motivo` obrigatório** em `saida`/`ajuste_saida`) | Bearer + módulo | `estoque.add_movimentoestoque` |
| GET | `/estoque/produtos/saldos/`, `/estoque/produtos/saldos/{produto}/` | Saldos dos produtos que controlam estoque (`q`, `situacao=zerado`, `ordenar`) | Bearer + módulo `estoque` | `estoque.view_movimentoestoque` |
| GET/POST | `/estoque/produtos/movimentos/`, `/estoque/produtos/movimentos/{id}/` | Histórico (`produto`, `tipo`, `data_de`, `data_ate`, `q`) e entrada/baixa manual (com `motivo`)/ajuste de produto; histórico imutável | Bearer + módulo | `estoque.view/add_movimentoestoque` |
| GET/POST | `/estoque/transferencias/`, `/estoque/transferencias/{id}/` | Transferências matriz ↔ filiais (origem = unidade do usuário) | Bearer + módulo | `estoque.view/add_transferenciaestoque` |
| GET/POST | `/financeiro/categorias/`, `/financeiro/categorias/{id}/` (+ `ativar/`, `inativar/`) | Categorias de receita/despesa (sem edição de nome; exclusão segura) | Bearer + módulo `financeiro-categorias` | `financeiro.*_categoriafinanceira` |
| GET/POST | `/financeiro/contas-pagar/`, `/financeiro/contas-receber/` (+ `/{id}/`) | Contas (filtros `q`, `categoria`, `situacao`, `vencimento_de/ate`); DELETE só sem baixas | Bearer + módulos `financeiro-pagar`/`financeiro-receber` | `financeiro.*_contapagar` / `*_contareceber` |
| POST | `/financeiro/contas-*/{id}/baixas/` e `/{id}/baixas/{baixa}/estorno/` | Registra baixa (total/parcial) e estorno (imutáveis) | Bearer + módulo | `financeiro.change_conta*` |
| GET | `/financeiro/dre/`, `/financeiro/balanco/` | DRE (`inicio`, `fim`, `regime`) e balanço (`data`) **gerenciais**, em centavos | Bearer + módulo `financeiro-relatorios` | `financeiro.view_relatoriofinanceiro` |
| GET | `/painel/` | Indicadores do painel (`periodo`, `inicio`, `fim`); cada bloco só com permissão | Bearer | autenticado |
| GET/POST | `/custos-comuns/` | Custos comuns: padrões do sistema (somente leitura) + os da empresa. Filtros `q`, `tipo`, `origem` (`sistema`/`proprio`), `ativo`, `ordenar` | Bearer + módulo `custo-comum` | `custo.view_customodelo` / `add_customodelo` |
| GET/PUT/PATCH/DELETE | `/custos-comuns/{id}/` | Consulta / atualiza (só os próprios; padrão do sistema = 404) / exclui (em uso: só desativa) | Bearer + módulo | `view` / `change` / `change` / `delete` |
| GET | `/estoque/movimentos/`, `/estoque/movimentos/{id}/` | Movimentações do cliente do usuário (filtros `insumo`, `tipo`, `data_de`, `data_ate`, `q`, `ordenar`) | Bearer + módulo `estoque` | `estoque.view_movimentoestoque` |
| PUT/PATCH | `/estoque/movimentos/{id}/` | **Corrige** a movimentação: `quantidade`, `medida`, `data`, `observacao`, `validade` (omitida = mantém, `null` = remove); recalcula saldos; 422 se o saldo ficaria negativo ou se for transferência | Bearer + módulo | `estoque.change_movimentoestoque` |
| GET | `/custo-operacional/categorias/` | Distribuição do custo operacional por categoria, em **centavos** (totais + lista; origem `vendas`/`igual`/`manual`/`zero`) | Bearer + módulo `custo-operacional` | `custo.view_custooperacional` |
| PUT/DELETE | `/custo-operacional/categorias/{id}/ajuste/` | Fixa (`{"valor": 20000}`) ou restaura o valor da categoria | Bearer + módulo | `custo.change_custooperacional` |

O recurso `/insumos/` ganhou o campo **`marca`** (texto de até 60 caracteres, opcional; vazio é válido; `<`, `>` e aspas duplas são recusados com 400). Os endpoints de listagem seguem a paginação padrão (`pagina`, `por_pagina`; dados em `data.itens`). Todos os escopos vêm do usuário autenticado (nunca de parâmetro): outra empresa recebe 404.

Todos os módulos de negócio têm API. Os recursos de cadastro (`produtos`, `canais-venda`, `custos-operacionais`, `processados`, `fichas-tecnicas`, `precificacoes`) e o financeiro **validam e gravam com os mesmos formulários e serviços das telas** (`apps/api/negocio.py` explica o adaptador): mesmas regras, mesma auditoria. PUT/PATCH funcionam mesclando o enviado com o valor atual; `itens` e `taxas` enviados **substituem** a lista inteira. O escopo é sempre a empresa do usuário autenticado (nunca um id enviado): registro de outra empresa responde 404. Para saber o formato exato de cada corpo e resposta, use o Swagger (`/api/docs/`). **Limites**: as telas de matriz/filiais (escolher a unidade nos relatórios) não têm equivalente — a API sempre usa a empresa do usuário; exportações Excel/PDF e as telas de gerenciamento de categorias padrão (`financeiro/categorias/padrao`) não foram expostas.

Segurança da API: veja [Segurança](#segurança).

## Swagger

Documentação interativa gerada **automaticamente do código** (serializers e views), sem texto duplicado:

| Endereço | O que é |
|---|---|
| `/api/docs/` | Swagger UI — veja e **teste** os endpoints |
| `/api/redoc/` | Mesma documentação em formato de leitura |
| `/api/schema/` | Arquivo OpenAPI (`?format=json` ou YAML) |

Só **administradores logados no sistema web** acessam essas páginas (o schema descreve toda a API, então não é público). Para testar: faça login em `/api/v1/auth/login/` pelo próprio Swagger, copie o `token`, clique em **Authorize** e cole o token.

Para gerar o arquivo do schema pela linha de comando: `python manage.py spectacular --file schema.yml --validate`.

---

## Auditoria

Toda operação de **criar, atualizar e excluir** (pela tela ou pela API) é registrada na tabela `auditoria_registroauditoria`: quem, quando, módulo, tabela, id e nome do registro, e — nas atualizações — **somente os campos que mudaram, com valor anterior e novo**.

Exemplos do que é gravado:

```
Criado insumo ID 123 - Queijo Mussarela.

Atualizado insumo ID 123 - Queijo Mussarela.
Alterações:
- Valor pago: R$ 35,00 → R$ 40,00
- Quantidade comprada: 10 → 15
- Unidade de medida: Quilo → Gramas

Insumo ID 123 - Queijo Mussarela foi inativado.        (exclusão lógica)
Excluído medida ID 18 - Pitada.                        (exclusão física)
```

Também são auditados: login/logout, exportações, papéis e permissões, vínculo de usuários a clientes, módulos habilitados e perfis disponíveis. Salvar sem mudar nada **não** gera registro. **Senhas nunca aparecem** (nem o hash): uma troca de senha aparece apenas como "Senha: alterada".

**Consulta:** menu **Auditoria** (`/auditoria`) — listagem paginada, pesquisa, filtros (usuário, módulo, ação, período) e detalhes com a tabela Campo | Antes | Depois. O registro é imutável (nem o admin do Django edita). Administradores globais veem tudo; os demais veem só o próprio cliente.

**Como funciona por dentro:** o serviço único está em `apps/auditoria/utils.py` (`registrar_criacao`, `registrar_edicao`, `registrar_exclusao`). Em views de formulário basta usar o `AuditoriaFormMixin`. A operação e a auditoria acontecem **na mesma transação**: se não der para auditar, a operação é desfeita (nunca existe alteração sem rastro). Eventos de login/logout são "silenciosos": uma falha ao auditar não derruba o login.

---

## Insumos

**Insumo** = matéria-prima comprada por um cliente. **Insumos não têm categoria** (as categorias pertencem aos produtos; a coluna antiga foi mantida no banco, oculta e opcional, só para não perder dados).

No cadastro você informa: **nome**, **unidade comprada**, **quantidade comprada** e **valor pago**. O sistema calcula e guarda o **preço por KG / L / UN**:

| Você informa | O sistema calcula |
|---|---|
| 100 gramas por R$ 4,00 | R$ 4,00 ÷ 100 g = R$ 0,04/g → ×1000 = **R$ 40,00 por KG** |
| 500 mililitros por R$ 10,00 | **R$ 20,00 por L** |
| 12 unidades por R$ 12,00 | **R$ 1,00 por UN** |
| 2 colheres de sopa por R$ 3,00 | (2×15 ml = 30 ml) → **R$ 100,00 por L** |

Na tela, abaixo da quantidade aparece **"100 gramas"** (por extenso, com singular/plural), e o rótulo muda conforme a unidade: **PREÇO (por KG)**, **(por L)** ou **(por UN)**. A prévia é calculada pelo servidor (`/medidas/calcular`) com as mesmas funções que gravam o valor — nunca diverge.

Como o dinheiro é guardado:

| Campo | Exemplo | Observação |
|---|---|---|
| `quantidade` | `100` | decimal (4 casas) |
| `preco_pago` | `400` | **centavos** (BIGINT) |
| `preco_unidade` | `4000` | **centavos** por KG/L/UN, recalculado a cada gravação |

A "exclusão" de um insumo é **lógica** (só inativa). Listagem com pesquisa, ordenação, paginação e exportação para Excel/PDF.

No cadastro há **Salvar** e **Salvar e adicionar outro** (volta ao formulário em branco; mixin `SalvarEAdicionarMixin` + `templates/partials/botoes_salvar.html`, reutilizáveis nos próximos módulos). Insumos usados em receitas não podem ser excluídos nem trocar de dimensão (kg → L), e insumos **gerados por processados** só mudam pelo processado. Alterar preço/quantidade/unidade de um insumo recalcula os processados que dependem dele — veja `apps/processado/README.md`.

## Categorias e Canais de venda

- **Categorias** (`apps/categoria/README.md`): ativar/desativar (sem cascata), excluir seguro (em uso → só desativa), quantidade vendida, faturamento, ticket médio e % do faturamento total (valores neutros sem faturamento).
- **Canal de vendas** (`apps/canal/README.md`, Configurações): nome, descrição e taxas percentuais e/ou fixas; o sistema calcula total de taxas e líquido (ex.: 26,20% + 1,59% = 27,79%, líquido 72,21%).

## Produtos e Fichas técnicas

- **Produtos** (`apps/produto/README.md`): cadastro por unidade com categoria; lista com filtros, ordenação, paginação e exportação Excel/PDF; exclusão segura (em uso → só desativa).
- **Fichas técnicas** (`apps/ficha/README.md`): itens (insumos/processados) com unidade, quantidade e custo por linha, custo total no topo e no rodapé, busca no seletor de insumos, exportação Excel/PDF. Recalculadas automaticamente quando um insumo ou processado muda.
- Infraestrutura comum: `web_project/exportacao.py` (Excel/PDF), `web_project/mixins.py` (`ListaPaginadaMixin`, `ExcluirOuDesativarView`, `em_uso`, `SalvarEAdicionarMixin`) e `src/assets/js/form-salvar.js`.

## Precificação

`apps/precificacao` (`apps/precificacao/README.md`): preço de venda = custo da ficha + custo operacional, com taxas do canal (cadastrado ou personalizado) e lucro por **margem** (sobre o preço), **markup** (sobre o custo) ou **valor em reais**, mais lucro e CMV. As fórmulas, a ordem das operações e os casos de borda estão documentados lá. Recalcula sozinha quando um insumo, processado, ficha ou canal muda.

## Financeiro

`apps/financeiro` (`apps/financeiro/README.md`): contas a pagar e a receber com baixas parciais/totais e estornos (histórico imutável), categorias financeiras, **DRE gerencial** (caixa ou vencimento) e **balanço gerencial** simplificado, com aviso de que não são demonstrações contábeis oficiais, e notificações de contas atrasadas/vencendo (`gerar_alertas_financeiro`, uma vez por conta por dia).

## Estoque

`apps/estoque` (`apps/estoque/README.md`): saldos por insumo e lote (com validade), entradas, saídas pelo PEPS (vence antes sai antes), ajustes, **transferências matriz ↔ filiais** (só usuários autorizados), mínimo por insumo e alertas de estoque baixo/vencimento (notificações idempotentes; comando `gerar_alertas_estoque`). Histórico imutável, saldo nunca negativo (transação + bloqueio de linha + `CheckConstraint`) e conferência por `verificar_estoque`.

## Custo operacional

`apps/custo` (`apps/custo/README.md`): custos fixos e variáveis por unidade, catálogo global de custos comuns (água, energia... sem copiar para os clientes) e distribuição por categoria (soma 100 %). Custo por unidade da categoria = custo do mês ÷ quantidade vendida da categoria; é sugerido na Precificação. Fórmulas, validações e tabelas no README do app.

## Processados e receitas

Módulo `apps/processado` (menu **Processados**): ficha técnica com insumos, unidade e quantidade, custo por item e total, rendimento, custo por KG/L e fator de rendimento; o resultado é publicado como insumo. O **recálculo em cascata** (vários níveis, ciclos, tenant, atomicidade, comando `recalcular_custos`) e as tabelas `processado_processado` / `processado_processadoitem` estão documentados em `apps/processado/README.md`.

## Módulos de negócio e como se conectam

```text
Medida ──► Insumo ──► Processado (insumo gerado) ──┐
              │   └──► Ficha técnica ◄── Produto ◄─┤── Categoria
              │            │                       │
              │            ▼                       │
Canal de venda ──► Precificação ◄── Custo operacional (sugestão por categoria)
              ▼
           Estoque (lotes, validade, transferências)  ──►  Financeiro (contas, DRE, balanço)  ──►  Painel
```

- **Recálculo em cascata** (`apps/processado/recalculo.py`): alterar um insumo recalcula, numa transação, processados (vários níveis), fichas e precificações (ganchos `GANCHOS_POS_RECALCULO`); alterar um canal reprecifica as precificações dele. `manage.py recalcular_custos` refaz tudo (idempotente).
- **Escopo matriz/filial** (`web_project/acesso.py`, `web_project/mixins.py`): a unidade vê só os próprios dados; a matriz vê/age nas filiais com `cliente.<ação>_itensfilial` (ver `apps/acl/README.md`).
- **Histórico imutável** (`web_project/historico.py`): movimentos de estoque, transferências e baixas de contas só crescem; correção = ajuste/estorno.
- **Notificações do sistema** (`notificacao.services.enviar_para_usuarios`): alertas de estoque e de contas, uma vez por item por dia.
- **Infraestrutura comum**: `web_project/exportacao.py` (Excel/PDF), `ListaPaginadaMixin`, `ExcluirOuDesativarView`, `SalvarEAdicionarMixin`, `src/assets/js/form-salvar.js`.

| Módulo | Documentação | Tabelas principais |
|---|---|---|
| Insumos / Medidas | este README | `insumo_insumo`, `medidas` |
| Processados | `apps/processado/README.md` | `processado_processado`, `processado_processadoitem` |
| Categorias | `apps/categoria/README.md` | `categoria_categoria` |
| Canais de venda | `apps/canal/README.md` | `canal_canalvenda`, `canal_canaltaxa` |
| Produtos | `apps/produto/README.md` | `produto_produto` |
| Fichas técnicas | `apps/ficha/README.md` | `ficha_ficha`, `ficha_fichaitem` |
| Precificação | `apps/precificacao/README.md` | `precificacao_precificacao` |
| Custo operacional | `apps/custo/README.md` | `custo_customodelo`, `custo_custooperacional`, `custo_custorateio` |
| Estoque | `apps/estoque/README.md` | `estoque_lote`, `estoque_movimentoestoque`, `estoque_transferenciaestoque`, `estoque_estoqueminimo`, `estoque_alertaenviado` |
| Financeiro | `apps/financeiro/README.md` | `financeiro_categoriafinanceira`, `financeiro_conta{pagar,receber}`, `financeiro_baixa{pagar,receber}`, `financeiro_alertafinanceiro` |
| Painel | `apps/dashboard/README.md` | `dashboard_presenca` |

### Atualizando um ambiente existente

```bash
python manage.py migrate          # aplica todas as migrations novas (várias criam módulos no catálogo)
python manage.py seed_acl         # permissões padrão por perfil (transferências e relatórios só Administrador/Gestor)
```

**Esta atualização** (marca do insumo, edição de estoque, custo operacional automático, custos comuns, login e PDFs) acrescenta as migrations abaixo — aplique **na ordem do `migrate`**, que resolve as dependências:

| App | Migration | O que faz | Reversível |
|---|---|---|---|
| `insumo` | `0007_insumo_marca` | coluna `marca` (varchar 60, padrão vazio) | sim |
| `estoque` | `0002_movimento_editado_em` | coluna `editado_em` (nula) | sim |
| `custo` | `0003_custos_comuns_por_cliente_e_ajuste_manual` | `cliente_id` (nulo) e datas em `custo_customodelo`, novas restrições de nome e a tabela `custo_custoajustecategoria` | sim |
| `custo` | `0004_rateios_viram_ajustes_manuais` | converte os rateios por percentual em ajustes manuais em R$ (dados) | sim (só apaga os ajustes criados) |
| `precificacao` | `0004` e `0005` | coluna `custo_operacional_manual`; valores antigos > 0 viram "manuais" | sim |
| `modulo` | `0009_modulo_custo_comum`, `0010_icones_do_menu` | módulo "Custos comuns" (Configurações) e ícones do menu (só onde ainda é o genérico) | sim |

Depois do `migrate`, um `post_migrate` completa as permissões padrão dos perfis para o módulo novo (só acrescenta) e normaliza insumos gerados por processados. **Rollback**: `python manage.py migrate <app> <migration anterior>`; nenhuma migration apaga dados (a tabela `custo_custorateio` foi mantida, sem uso). Antes de produção, faça backup do banco. Cada tabela/coluna afetada está descrita no README do app (`apps/insumo`, `apps/estoque`, `apps/custo`, `apps/precificacao`, `apps/ficha`).

As migrations de tabelas que antes eram só "âncora" de permissão (produto, ficha, precificação, processado) **param com mensagem** se houver registros antigos sem dono, em vez de inventar dados. Módulos novos de negócio são habilitados para clientes que já têm segmento, sem sobrescrever vínculos existentes; para um ajuste manual use `python manage.py seed_modulos`.

### Atualização: Evolution API, templates do WhatsApp, retenção de notificações e tours

```bash
python manage.py migrate integracao      # 0005 e 0006 (aditivas; 0006 cria a tabela e as permissões dos templates)
```

| App | Migration | O que faz | Reversível |
|---|---|---|---|
| `integracao` | `0005_provedor_whatsapp_evolution` | só atualiza as `choices` de `integracao_conexao.provedor` (provedor `whatsapp_evolution`); sem mudança de esquema | sim |
| `integracao` | `0006_template_whatsapp` | cria `integracao_templatewhatsapp` (colunas, chaves e índices no `apps/integracao/README.md`) e concede `*_templatewhatsapp` ao Administrador (4) e ao Gestor (3) | sim (remove a tabela; as permissões ficam órfãs e inofensivas) |

Notificações **não** exigem migração: a coluna `lida_em` já existia. Agende o novo comando (a cada hora):

| Comando | Para que serve |
|---|---|
| `limpar_notificacoes_lidas` | apaga as notificações lidas há 48 h ou mais (as expiradas já não aparecem nas telas/API mesmo antes de rodar) |

Detalhes: Evolution API e templates em `apps/integracao/README.md` e `apps/integracao/GUIA_INTEGRACOES.md` (seções 1 e 2A); retenção em `apps/notificacao/README.md`; tours em `apps/tour/README.md`. A API nova (`/api/v1/whatsapp-templates/`, `/api/v1/notificacoes/`) está no Swagger (`/api/docs/`).

### Atualização: agenda, canais/formas globais, serviços, orçamento e cadastro de clientes

```bash
python manage.py migrate        # aplica na ordem do grafo (tudo aditivo)
python manage.py seed_acl       # opcional: reaplica as permissões padrão (a migração do orçamento já concede aos perfis existentes)
```

| App | Migration | O que faz | Reversível |
|---|---|---|---|
| `cliente` | `0010_contato_da_empresa` | `endereco`, `cidade`, `uf`, `email_contato`, `whatsapp` (opcionais, padrão vazio) em `cliente_cliente` | sim |
| `canal` | `0002_escopo_global` | `canal_canalvenda.eh_global` (padrão `false`) + tabela `canal_canalvenda_segmentos` | sim |
| `pdvconfig` | `0003_escopo_global` | `pdvconfig_formapagamento.eh_global` (padrão `false`) + `pdvconfig_formapagamento_segmentos` | sim |
| `produto` | `0004_tipo_servico` | `produto_produto.tipo` (`produto`/`servico`, padrão `produto`) e **converte para `servico`** os produtos de clientes do segmento Serviço | sim (não apaga dados) |
| `ficha` | `0004_mao_de_obra_na_ficha` | tabela `ficha_fichamaodeobra` (mão de obra nas fichas de serviço) | sim |
| `orcamento` | `0001_initial` | tabelas `orcamento_orcamento`, `_orcamentoitem`, `_orcamentoenvio`, `_orcamentolink`; cria e concede as permissões do módulo aos perfis existentes | sim (remove as tabelas) |
| `modulo` | `0024_modulo_orcamento` | acrescenta “Orçamentos” ao catálogo (módulo essencial: só depende da permissão) | sim |

Colunas, tipos, índices e regras de cada tabela estão no README do app (`apps/cliente`, `apps/canal`, `apps/pdvconfig`, `apps/produto`, `apps/ficha`, `apps/orcamento`). **Comportamentos que mudam e merecem atenção:** o cadastro de cliente passa a exigir nome/usuário/e-mail do responsável (API: `usuario_nome` e `usuario_username` novos e obrigatórios na criação); a primeira sincronização do Google Agenda depois da atualização pode reescrever uma vez cada evento com hora (o hash passou a comparar instantes em UTC); o catálogo do PDV deixou de cortar em 80 itens. Faça backup do banco antes de produção. Para enviar orçamento por e-mail configure `RESEND_API_KEY` e `MAIL_FROM_EMAIL`; para WhatsApp use uma conexão ativa em Integrações e `BASE_URL` (links do PDF); mantenha `enviar_mensagens_pendentes` agendado a cada minuto.

### Rotinas agendáveis (cron) e configurações

| Comando | Para que serve |
|---|---|
| `recalcular_custos [--cliente ID]` | refaz processados, fichas e precificações (nova tentativa idempotente) |
| `gerar_alertas_estoque [--cliente ID]` | notifica estoque baixo e vencimentos (rodar diariamente) |
| `gerar_alertas_financeiro [--cliente ID]` | notifica contas atrasadas e vencendo (rodar diariamente) |
| `verificar_estoque [--corrigir]` | confere o saldo dos lotes contra o histórico |

| Configuração (`settings`) | Padrão | Efeito |
|---|---|---|
| `ESTOQUE_DIAS_ALERTA_VENCIMENTO` | 7 | prazo do alerta de lote a vencer |
| `FINANCEIRO_DIAS_ALERTA_VENCIMENTO` | 3 | prazo do alerta de conta vencendo |

---

## Medidas

Tabela `medidas` (cadastro **global** do sistema, só para administradores — menu **Medidas**). Vem com 17 unidades, nesta ordem: Gramas, Quilo, Unidade, Mililitro, Litro, Colher de chá, Colher de sopa, Colher de café, Copo americano, Xícara, Polegadas, Centímetro, Metro, Metro quadrado, Metro Cúbico, Segundos, Quilômetro.

Campos: `id`, `medida`, `sigla`, `descricao`, `equivale` (texto de exibição, ex.: "1000 g = 1 Kg"), `ativo`, `id_usuario` (quem cadastrou), `criado_em`, `atualizado_em`. Além desses, foram acrescentados campos **necessários para calcular** (o texto de `equivale` não dá para usar em contas):

- `dimensao`: massa, volume, comprimento, área, tempo ou unidade. **Só se converte dentro da mesma dimensão** (não existe "kg → litro").
- `fator_base`: quanto vale 1 desta unidade na unidade base da dimensão. Ex.: quilo = 1000 (base: grama); litro = 1000 (base: ml); colher de sopa = 15 (ml); polegada = 0,0254 (base: metro).
- `nome_singular` / `nome_plural`: para escrever "1 colher de sopa" / "2 colheres de sopa".

Valores de referência de cozinha (editáveis na tela): colher de chá 5 ml, colher de sopa 15 ml, colher de café 2 ml, copo americano 200 ml, xícara 240 ml.

### Como a conversão funciona

Converte-se sempre passando pela unidade base: `quantidade × fator_origem ÷ fator_destino`. Exemplos: 1000 g → 1 kg; 100 g → 0,1 kg; 500 ml → 0,5 L. Qualquer par de unidades da mesma dimensão funciona sem tabela de conversões fixa no código.

### Regra de exclusão (sem CASCADE)

O pedido era "excluir/editar em cascata", mas **apagar uma medida em cascata apagaria os insumos que a usam** (e o histórico de preços). Por isso:

- medida **sem uso** → exclusão física (em transação);
- medida **em uso** → apenas **inativada** (some dos formulários novos, mas os registros antigos continuam válidos);
- medida em uso **não pode ter dimensão nem fator alterados** (isso mudaria o significado dos preços já gravados). Para mudar de verdade: cadastre uma nova medida e inative a antiga.

Qualquer novo model que aponte para `Medida` passa a contar como "uso" automaticamente (a regra percorre as relações do Django).

---

## Regras de negócio

Todos os cálculos e validações reutilizáveis moram em **`web_project/regras/`** — nunca em views, templates ou JavaScript. São módulos de funções simples e **sem acesso ao banco** (por isso fáceis de testar).

| Arquivo | O que tem |
|---|---|
| `dinheiro.py` | `DinheiroService`: centavos ↔ Decimal (`para_decimal`, `para_centavos`, `de_texto`), `formatar` ("R$ 1.250,75"), `para_api`. Recusa `float`. Leitura de "R$ 1.234,56" |
| `conversao.py` | `converter(quantidade, origem, destino)`, `preco_por_unidade_padrao(centavos, quantidade, medida)`, validação de quantidade, dimensões |
| `extenso.py` | `quantidade_por_extenso` ("100 gramas", "1 quilo", "0,5 litro"), `moeda_por_extenso` (10000 → "Cem reais"), `numero_por_extenso` |
| `validadores.py` | CPF, CNPJ, telefone, celular, e-mail, datas brasileiras |
| `indicadores.py` | Indicadores financeiros (próxima seção) |

Princípios aplicados: dinheiro persistido em **centavos inteiros** e calculado em **Decimal** (nunca float), arredondamento comercial (meio centavo para cima) só no resultado final, divisão por zero impedida (levanta `RegraNegocioError`, que a tela/API mostram como mensagem amigável).

## Máscaras

Componente único `src/assets/js/mascaras.js`, aplicado com um atributo no campo: `<input data-mask="cpf">`. Tipos: `cpf`, `cnpj`, `cpf-cnpj`, `telefone`, `celular`, `moeda`, `decimal`, `inteiro`, `data`, `datahora`. A moeda funciona "como calculadora" (digitar `400` mostra `4,00`).

> **Máscara não substitui validação.** O navegador só ajuda a digitar; o servidor revalida tudo (`web_project/regras/validadores.py`, `DecimalBRField`, `MoedaCentavosField`).

Já aplicadas em Insumos (quantidade, valor) e Clientes (CPF/CNPJ). Para usar em outro formulário: adicione `data-mask="..."` ao widget do campo.

## Indicadores

Funções em `web_project/regras/indicadores.py`. Entradas e saídas são `Decimal`; valores em reais; percentuais em pontos percentuais (30 = 30%). Entrada inválida ou divisão por zero levanta `RegraNegocioError`.

| Indicador | Fórmula | Observação |
|---|---|---|
| Custo unitário (por KG, litro, unidade) | custo total ÷ quantidade | informe a quantidade já na unidade desejada (use `converter`) |
| Ticket médio | faturamento ÷ número de vendas | |
| Lucro do produto | preço de venda − custo | |
| Markup (%) | (preço − custo) ÷ custo × 100 | base = **custo** |
| Preço por markup | custo × (1 + markup/100) | |
| Margem de lucro (%) | (preço − custo) ÷ preço × 100 | base = **preço** (nunca passa de 100%) |
| Preço por margem | custo ÷ (1 − margem/100) | margem deve ser < 100% |
| CMV | estoque inicial + compras − estoque final | |
| Ponto de equilíbrio (unidades) | custos fixos ÷ (preço − custo variável unitário) | exige preço > custo variável |
| Ponto de equilíbrio (R$) | unidades de equilíbrio × preço | |
| Fator de cocção | peso depois ÷ peso antes | 1000 g → 700 g = 0,7 |
| Fator de redução (%) | (1 − fator de cocção) × 100 | 1000 g → 700 g = 30% |

> Hoje o sistema só usa na tela o cálculo de custo por unidade (insumos). Os demais indicadores estão prontos, testados e centralizados para quando os módulos de produto, ficha técnica e precificação forem implementados.

---

## Encoding (UTF-8)

O sistema usa **UTF-8 de ponta a ponta** (banco, Python, templates, cabeçalhos HTTP, JSON da API, exportações). Isso evita textos como `PapÃ©is` (que é "Papéis" lido com a codificação errada).

A causa real do problema que existia: o menu era lido de um arquivo JSON com `open()` sem informar a codificação — no **Windows** isso usa cp1252 em vez de UTF-8. Foi corrigido na origem (`read_text(encoding="utf-8")`), e há testes que impedem a volta do erro.

**Regra para quem programa:** ao ler ou gravar arquivos em Python, **sempre** informe `encoding="utf-8"`. Nos templates, `<meta charset="UTF-8">` já está no layout. No PostgreSQL, crie o banco em UTF-8.

Também foi corrigido o erro do tema `Language "pt-br" not found!`: o Vuexy só traz en/fr/ar/de; o `pt-br` agora é registrado em `src/assets/js/template-customizer-pt-br.js` (carregado antes de o customizer iniciar), sem editar o arquivo original do tema.

---

## Segurança

Resumo do que existe (todas as checagens ocorrem **no servidor**):

- **Autenticação e senhas:** hash PBKDF2, validadores de senha em todos os caminhos (cadastro, redefinição, alteração, usuários da API e da tela), tokens de redefinição de uso único com validade, mensagens iguais para "usuário inexistente" e "senha errada" (sem enumeração de contas).
- **Rate limit:** login web e da API (por IP + login), recuperação de senha, e *throttling* nos endpoints sensíveis e gerais da API (`429`).
- **IP confiável:** `X-Forwarded-For` só é lido se `TRUST_X_FORWARDED_FOR=True` e usa o **último** valor (o IP que o nosso proxy viu); caso contrário um cliente poderia forjar o cabeçalho para escapar do limite de tentativas.
- **Autorização:** permissões nativas do Django por tela e por ação (web e API), módulo habilitado por cliente, e `403` quando falta algo.
- **Isolamento por cliente (IDOR):** consultas sempre filtradas pelo cliente do usuário logado; id de outro cliente → `404`. Testado para insumos, categorias, usuários, auditoria e API.
- **Mass assignment:** os serializers/forms listam campos explicitamente; `cliente`, `usuario`, `preco_unidade`, `is_superuser`... nunca são aceitos da entrada.
- **SQL Injection:** só ORM do Django (nenhum SQL cru); ordenação por *whitelist*.
- **XSS:** autoescape dos templates; exportação Excel neutraliza fórmulas (`=`, `+`, `-`, `@`).
- **CSRF:** ativo nas telas; a API usa token no cabeçalho (sem cookie de sessão, logo sem CSRF).
- **Dados sensíveis:** a API nunca devolve senha, hash ou tokens de terceiros; a auditoria nunca grava senha.
- **Produção (`DEBUG=False`):** redireciona para HTTPS, HSTS, cookies seguros, proteção de MIME e de *clickjacking*; `DEBUG` vem **desligado** por padrão; `ALLOWED_HOSTS` por variável de ambiente.
- **Documentação da API** restrita a administradores logados.
- **Integridade:** transações em exclusões, criação de usuário, módulos e auditoria; valores monetários sem float.

## Testes

```bash
python manage.py test                      # tudo (leva alguns minutos: o hash de senha é propositalmente lento)
python manage.py test apps.api             # só a API
python manage.py test apps.insumo.tests    # um módulo
```

Cobrem: regras de negócio (dinheiro, conversão, extenso, validadores, indicadores), medidas e carga inicial, insumos (cálculos, validações, isolamento, exportações, **migração de dados para frente e para trás**), auditoria (CREATE/UPDATE/DELETE, atomicidade, telas), usuários, API (login, token expirado, rate limit, CRUD, permissões, 401/403/404/409/422, isolamento entre clientes, varredura "nenhum endpoint responde sem token", documentação), segurança (IP forjado, enumeração) e desempenho (**sem N+1**: o número de consultas é constante).

---

## Manutenção

### Adicionar um novo módulo (tela com CRUD)

1. `python manage.py startapp nome` dentro de `apps/` e registre em `INSTALLED_APPS` (`config/settings.py`).
2. Crie o model (com `cliente` FK se os dados são por empresa). Dinheiro: `PositiveBigIntegerField` em centavos e `AUDITORIA_MONETARIOS = ("campo",)`. `makemigrations` + `migrate`.
3. Views: use `ClienteScopedMixin` (isolamento) e `AuditoriaFormMixin` (auditoria de criar/editar) antes de `CreateView`/`UpdateView`. Na exclusão, chame `registrar_exclusao(...)` dentro de `transaction.atomic()`.
4. URLs com `module_required("slug", "app.permissao")`.
5. Cadastre o módulo em **Configurações > Módulos > Novo Módulo** (ou em `MODULOS_PADRAO` de `seed_modulos` + `python manage.py seed_modulos`): nome, ícone, rota, permissão, seção do menu e ordem. Isso já cria o item de menu e a linha na matriz de Papéis. Depois inclua o módulo nos **Segmentos** e habilite-o por cliente. Dê as permissões padrão em `seed_acl` (`SCREEN_APPS`). Passo a passo completo em `apps/modulo/README.md`.
6. Escreva testes (use `apps/insumo/tests.py` como modelo).

### Adicionar um endpoint na API

1. Serializer em `apps/api/serializers.py` (campos **explícitos**; reaproveite validações existentes em vez de copiar).
2. ViewSet em `apps/api/views.py` com `permission_classes = [PermissaoDoSistema]`, `permissao_app`, `permissao_model` e (se for por cliente) `modulo_slug`; filtre o queryset pelo cliente. Use `AuditoriaViewSetMixin` e, se tiver ativar/inativar, `AtivarInativarMixin`.
3. Registre em `apps/api/urls.py` (`router.register(...)`).
4. A documentação Swagger aparece sozinha; confira com `python manage.py spectacular --validate`.
5. Teste: autenticação (401), permissão (403), isolamento (404), validações (400/409/422) e que as consultas não crescem com a lista (N+1).

### Adicionar um novo cálculo

Crie a função em `web_project/regras/` (ex.: `indicadores.py`): entradas `Decimal`, validação, `RegraNegocioError` para entradas inválidas, comentário explicando a fórmula, e teste em `web_project/tests_regras.py`. Views, serializers e templates só **chamam** a função.

### Adicionar uma nova medida

Pela tela **Medidas** (administrador): informe nome, sigla, **dimensão** e **fator** (quanto vale em relação à unidade base) e os nomes no singular/plural. Pronto: conversões e preço por unidade passam a funcionar para ela. Para incluir na carga inicial de ambientes novos, adicione a linha em uma nova migration de dados (não altere a `0002`).

### Adicionar uma nova auditoria

- Tela com `CreateView/UpdateView`: use `AuditoriaFormMixin`.
- Exclusão: `registrar_exclusao(request, objeto, logica=True|False)` **antes** de apagar/inativar, na mesma transação.
- Mudança que não é campo simples (ex.: lista de permissões): monte as `alteracoes` (`[{"campo","rotulo","antes","depois"}]`) e chame `registrar_alteracoes`.
- Campos que não podem ir para o log (senhas): passe `ignorar=` em `tirar_foto`.

### Adicionar uma nova permissão

Cada model ganha `view/add/change/delete` automaticamente. Para uma permissão extra, declare em `Meta.permissions` do model, crie a migration e dê a permissão ao perfil desejado em **Papéis** (ou em `seed_acl`). Proteja a view/rota com ela (`module_required`/`screen_required` ou `permissoes_por_acao` na API).

### Tarefas rápidas

| Quero... | Faça |
|---|---|
| Ver quem fez o quê | menu **Auditoria** |
| Habilitar um módulo para um cliente | menu **Módulos** (ou `PUT /clientes/{id}/modulos/`) |
| Criar um perfil | menu **Papéis** |
| Atualizar dependências | `pip install -U <pacote>`, rode os testes e atualize o `requirements.txt` |
| Reaplicar perfis/módulos padrão | `python manage.py seed_acl` e `seed_modulos` |

---

## Decisões técnicas

- **Dinheiro em centavos (BIGINT) + Decimal nos cálculos:** elimina erros de ponto flutuante; todas as conversões passam por `DinheiroService`. A migração converteu `Insumo.preco` com validação antes de apagar a coluna antiga, avisa se houve fração de centavo e é reversível.
- **Medidas com `fator_base` e `dimensao`:** o texto `equivale` não permite cálculo; o fator numérico permite converter qualquer par de unidades sem tabela fixa no código.
- **Sem CASCADE em medidas:** apagaria insumos e histórico; medida em uso é inativada e fica travada contra mudança de fator.
- **Insumo sem categoria:** coluna mantida oculta e opcional para preservar dados.
- **`Cliente.perfis` (M2M):** o prompt exige que o perfil "pertença ao cliente", mas os papéis eram globais; a lista de perfis disponíveis por cliente resolve isso (clientes existentes receberam todos os perfis atuais, sem mudar o comportamento).
- **Auditoria e operação na mesma transação** (falha de auditoria desfaz a operação); eventos de login/logout em modo silencioso.
- **API com token (não JWT) e sem sessão:** simples, revogável (logout apaga) e imune a CSRF; um token por usuário (cada login invalida o anterior).
- **Resposta única `{success, message, data|errors}`** montada em um só lugar (renderer + tratador de exceções); a documentação Swagger é gerada do código e embrulhada no mesmo envelope por um *hook*.
- **Serviços compartilhados web/API** (`auth/services.py`, `apps/acl/services.py`, `apps/modulo/services.py`, `apps/usuario/services.py`, `apps/auditoria/filtros.py`): a regra vive em um lugar só.
- **Usuário da API reaproveita o `UsuarioForm`:** mesmas validações da tela, sem duplicar.
- **Usuários nunca são apagados**, só inativados.
- **Sem endpoints fictícios:** produtos/processados/fichas/precificação não têm dados, então não têm API.

## Atualização: melhorias de usabilidade, estoque, custos e relatórios

Resumo do que mudou nesta versão (detalhes e tabelas/colunas nos READMEs de cada app):

| Área | Comportamento novo |
|---|---|
| **Insumos** | Tabela sem Quantidade/Valor pago e com a coluna **Preço**; filtros (pesquisa com ícone, **status**, unidade) responsivos e compatíveis com paginação; campo opcional **Marca do produto** (validado, auditado e na API). |
| **Processados / Fichas / Insumos em dropdowns** | Insumo exibido como "Nome (Kg)" e unidade só com a **maior grandeza**; a ficha técnica deixou de ter nome digitado (usa o do produto). |
| **Estoque** | **Validade sem lote** (lote automático `VAL-AAAAMMDD`) e **edição de movimentação** com recálculo de saldos, sem saldo negativo e com auditoria. |
| **Custo operacional** | Distribuição **automática em R$ por vendas** (soma exata, centavos pelo maior resto), com **ajuste manual** preservado e "Restaurar". |
| **Configurações > Custos comuns** | CRUD com filtros, paginação, exportação, auditoria e desativação quando já usado. |
| **Precificação** | Coluna "Ficha técnica" oculta, visão em **cartões**, e custo operacional **preenchido pela distribuição** (marcado quando ajustado). |
| **Login** | **Lembrar-me** seguro (sessão de 30 dias, cookie HttpOnly/SameSite, revogação), `next` sem redirecionamento aberto, mensagens em português. |
| **Interface** | Alertas e confirmações com **SweetAlert2** centralizados (`src/assets/js/alertas.js`); **pesquisa em todos os dropdowns** (`selects-pesquisaveis.js`); ícones do menu coerentes com os módulos. |
| **PDFs** | Um único padrão (`web_project/exportacao.py::exportar_pdf`): faixa com o sistema e o título, emissão, filtros, cabeçalho de colunas repetido nas páginas, rodapé com a empresa e "Página X de Y". |

### Partes de código mais complexas

- **Distribuição do custo** (`apps/custo/calculos.py::distribuir_por_vendas`): método do maior resto em inteiros (sem float) — a soma das categorias fecha exatamente com o total; ajustes manuais fixam categorias e o resto é repartido entre as demais. Regras, período e limites estão em `apps/custo/README.md`.
- **Edição de movimentação** (`apps/estoque/services.py::editar_movimento`): `delta = sinal × (nova − antiga)` aplicado ao lote e ao "saldo após" dos movimentos posteriores, tudo bloqueado numa transação; ver `apps/estoque/README.md`. É a **única** porta que altera o histórico (`web_project/historico.py::corrigir_registro`).
- **Lembrar-me**: ver [Autenticação](#autenticação).
- **Pesquisa nos dropdowns**: `selects-pesquisaveis.js` inicia o select2 em todo `<select>` (e nos criados depois, via `MutationObserver`); exclua um campo com `data-sem-busca`; listas já chegam completas no HTML (as longas continuam com a busca no servidor que a página já tiver).
- **Alertas**: as páginas não desenham alertas. O Django entrega `messages` escondidas em `#mensagens-django` (`templates/layout/master.html`); `alertas.js` mostra sucesso/informação como aviso rápido e aviso/erro como caixa. Para confirmar uma ação, use `<form data-confirmar="Pergunta?">`; em scripts, `SFC.confirmar()` e `SFC.alerta()`.
- **PDF**: `exportar_pdf(nome_arquivo, titulo, usuario, cabecalho, linhas, filtros)` — as colunas de valores (R$, %, quantidades com sinal) alinham à direita, a largura das colunas é proporcional ao conteúdo e a empresa do rodapé é a do usuário que emite (os dados já chegam filtrados pelo escopo dele).

### Regras e fórmulas (resumo)

```text
custo da categoria = custo operacional total × vendas da categoria ÷ vendas das categorias elegíveis   (maior resto, em centavos)
custo operacional/unidade = custo da categoria ÷ quantidade vendida
edição de estoque: delta = sinal × (nova − antiga); saldo do lote e saldos posteriores + delta; recusa se algum < 0
```

### Segunda rodada: correções pontuais e API completa

| Módulo | Problema | O que mudou |
|---|---|---|
| **Processados** | A tabela da ficha técnica não mostrava o custo; faltava "Salvar e adicionar outro" e o valor por extenso; unidades só com sigla | A ficha agora calcula **custo por linha e total em tempo real** (`POST /processado/calcular`, mesma conta da gravação, só insumos do tenant); botão **Salvar e adicionar outro** (volta ao formulário em branco; erro não grava); abaixo do rendimento aparece **"100 gramas"** (`/medidas/calcular`); dropdowns de unidade com **todas** as opções e o **nome completo** ("Quilo (kg)"), mantendo o id como valor |
| **Ficha técnica** | Dropdown de unidades com sigla e só a maior grandeza | Mesma lista completa com nome completo; escolher o insumo ainda **pré-seleciona** a maior grandeza dele |
| **Estoque** | Configuração de mínimo difícil de achar | Na lista de saldos, ao lado do valor do mínimo, o atalho **Definir mínimo** (só para quem tem `change_movimentoestoque` e só nas unidades que pode alterar; o servidor revalida) |
| **Custos comuns** | Editar/Excluir não apareciam | Os botões aparecem onde o servidor aceita: itens da **própria empresa** (conforme a permissão de cada ação) e, para o **administrador do sistema**, também os **padrões do sistema** (que valem para todas as empresas). Outro usuário não altera padrões nem itens de outra empresa (404). O perfil Gestor não tem `delete_customodelo` por padrão, então não vê Excluir |
| **Login** | "Lembrar-me" não persistia | O cookie de sessão/CSRF deixa de ser `Secure` em desenvolvimento (`DEBUG=True`): navegadores que descartam cookies `Secure` recebidos por HTTP (ex.: IP da rede) perdiam até a sessão de 30 dias. Em produção continua `Secure`; `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE` no `.env` forçam o valor. Mensagens de erro em português e genéricas |
| **SweetAlert2** | Sucesso aparecia no canto | Sucesso agora é **caixa centralizada** como o erro (título "Tudo certo!", mesmo texto, 5 s com barra de progresso, botão OK e fechamento por clique/Esc); informação segue no canto |
| **API/Swagger** | Faltavam módulos | Veja a tabela de endpoints; `spectacular --validate` sem avisos |

### Terceira rodada: unidades, extenso, custo e nome

- **Unidade da linha** (Processados e Ficha técnica): começa em "Unidade..." e **não é mais pré-selecionada** ao escolher o insumo (removido ); sem unidade o custo mostra "Escolha a unidade".
- **Quantidade por extenso em cada linha** ("2 unidades", "100 gramas"), vinda do servidor junto com o custo; custo = preço × quantidade na unidade escolhida (Abacaxi a R$ 5/un, 2 un = R$ 10).
- **Custo operacional**: o campo Nome só aparece em "Custo próprio (sem modelo)"; com custo comum, o nome é o do dropdown (também no servidor).
- **Cache de scripts**:  (, disponível em todos os templates) coloca a data do arquivo na URL; depois de atualizar, o navegador baixa o script novo sozinho, sem Ctrl+F5. **Sem alteração de banco** nesta rodada.

**Atenção ao atualizar:** como `ficha-form.js`, `calculo-medida.js` e `alertas.js` mudaram, faça **Ctrl+F5** (recarga sem cache) nas telas depois de atualizar; o navegador pode reutilizar a versão antiga do arquivo. **Banco de dados: nenhuma tabela ou coluna foi criada ou alterada nesta rodada e não há migration nova** (as migrations da rodada anterior continuam necessárias).

### Verificações executadas

`python manage.py test` (suíte completa), `python manage.py makemigrations --check` (só a diferença do pacote externo `webpush`), `python manage.py spectacular --validate` (sem avisos) e conferência no navegador de login, filtros, dropdowns com pesquisa, alertas SweetAlert2, distribuição por categoria e cartões da precificação. Veja "Pendências" para o que não foi coberto.

## Pendências e pontos de atenção

1. **Todos os módulos pedidos foram implementados** (processados, categorias, canais, produtos, fichas, precificação, custo operacional, estoque, financeiro e painel). Limites conhecidos: DRE e balanço são **gerenciais** (não contábeis oficiais); não há módulo de **vendas**, então estoque e financeiro não se movem sozinhos a partir delas e as categorias usam faturamento/quantidade **informados**; "online" é aproximado (janela de 5 minutos); a **API REST** só expõe, entre os módulos novos, custos comuns, a distribuição do custo operacional e as movimentações de estoque (veja [API](#api)); o bloqueio de linha do estoque e das baixas só vale em PostgreSQL e a suíte não exercita transações realmente paralelas.
2. **PostgreSQL:** a configuração por variáveis de ambiente está pronta, mas a suíte de testes só foi executada em SQLite. Rode `python manage.py test` apontando para um PostgreSQL de teste antes de ir para produção.
3. **Cache/throttling em memória:** o rate limit e o *throttling* usam o cache do Django (`LocMemCache`, por processo). Com vários processos/servidores em produção, configure um cache compartilhado (Redis) em `CACHES`.
4. **Um token por usuário:** logar em um segundo aparelho desconecta o primeiro (decisão de simplicidade; evoluir para um token por dispositivo se necessário).
5. **CPF/CNPJ de usuário:** o `auth_user` não tem campo de documento (só o Cliente tem); por isso a validação de CPF/CNPJ se aplica ao cadastro de Clientes.
6. **Login web não exige e-mail verificado** (comportamento anterior mantido; o e-mail de verificação existe, mas a verificação não bloqueia o acesso).
7. **Permissões antigas de auditoria** (`add/change/delete_registroauditoria`) continuam existindo no banco de quem já tinha o sistema; são inofensivas (a tela só usa `view`).
8. **Interface:** a verificação visual das telas foi feita manualmente no navegador (insumos, medidas, usuários, filtro de perfil por cliente); não há testes automatizados de interface (clicar em botões).
9. **Valores de referência de cozinha** (copo americano 200 ml, xícara 240 ml, colher de café 2 ml) podem variar por região — ajuste na tela de Medidas conforme a sua realidade (só se a medida ainda não estiver em uso).
10. **Não foi feito teste de carga** (muitos usuários simultâneos).
11. **Evolução em fases (PDV, compras, cotações, clientes finais, pedidos, hora/dia, integrações, fiscal):** em andamento. **Fase 1 (receita e observações em Processados e Fichas, com PDF/Excel individual)** **Fase 2 (Consumidores / clientes finais, com ViaCEP e BrasilAPI e anonimização LGPD)** **Fase 3 (lista de compras com sugestões, UUID do insumo, etiquetas A4/térmica com QR Code e histórico do insumo)** **Fase 4 (cotações entre lojas com atacado, comparativo normalizado, vencedores, contas a pagar e PDF/Excel)** e **Fase 9 (fiscal: estrutura, certificado A1 cifrado, fila e documentos PENDENTES; sem emissão real)**, **Fase 8 (integrações: conexões cifradas, mensagens com fila, webhooks assinados e agenda; conectores NÃO homologados)**, **Fase 7 (mão de obra por hora e por dia, arredondamento por minuto e 50% de hora extra configuráveis, só para serviço)**, **Fase 6 (pedidos e cozinha: fila de produção, regra de confirmação por unidade, baixa de estoque por item sem duplicar)** e **Fase 5 (PDV: formas de pagamento com taxa percentual/fixa/ambas, caixa, vendas, comandas de mesa, devolução e comprovante não fiscal)** concluídas. Fases seguintes pendentes; a emissão fiscal (NFS-e Nacional, NFC-e, NF-e) depende de certificado, ambiente de homologação e credenciais do provedor, e **não** será simulada.
12. **Consumidores:** prazo de retenção automática dos dados pessoais **não definido** (a anonimização é manual); o consentimento LGPD é registrado mas não bloqueia o cadastro; os provedores ViaCEP e BrasilAPI são gratuitos e sem SLA (a tela funciona com digitação manual se estiverem fora). Veja `apps/consumidor/README.md`.
13. **Etiquetas/QR:** o layout do PDF (A4 3×8 e térmica 50×30) foi gerado e testado automaticamente, mas **não foi conferido impresso**; imprima uma folha de teste e ajuste as medidas em `apps/estoque/etiquetas.py` se a sua folha/impressora diferir. Configure `BASE_URL` no `.env`: é o endereço gravado no QR (trocá-lo depois invalida os QR já impressos).
14. **Cotações:** a interpretação dos campos de atacado e o critério de comparação (menor preço normalizado por KG/L/UN, vencedor escolhido manualmente) estão em `apps/cotacao/README.md` e dependem da sua confirmação; o envio da cotação às lojas é só uma mudança de status (não há e-mail/WhatsApp automático nesta fase).
15. **PDV:** o preço e a ficha de venda vêm da Precificação marcada *Usar no PDV* (uma por produto); impressão térmica direta (ESC/POS) e o layout 58/80 mm foram testados por software, **não em hardware**; a emissão fiscal (NFC-e/NF-e/NFS-e) **não existe ainda** — o comprovante é não fiscal (Fase 9). Taxas de pagamento são informativas (relatórios do caixa), não geram despesa no financeiro. Veja `apps/pdv/README.md`.
16. **Integrações:** WhatsApp, Instagram, Google Agenda e iCloud foram implementados pela documentação dos provedores e testados só com um provedor simulado; **não foram homologados com contas reais** e dependem de credenciais próprias, aprovação dos provedores (Meta, Google) e da chave `INTEGRACAO_CRYPTO_KEYS`. iFood e 99Food: apenas a estrutura de adaptadores. Veja `apps/integracao/README.md`.
17. **Fiscal:** o sistema **não emite** NFC-e, NF-e nem NFS-e ainda. Há cadastro fiscal, certificado A1 cifrado, pedido de documento e fila, mas nenhum provedor concluído: os documentos ficam PENDENTES com o motivo e o comprovante do PDV é NÃO FISCAL. Faltam layout/XSD oficiais, assinatura XMLDSig, comunicação com autenticação mútua e homologação com certificado de teste (NFS-e Nacional); NFC-e/NF-e exigem provedor por UF (SEFAZ). Veja `apps/fiscal/README.md`.

## Imagens (Cloudflare R2)

Armazenamento e entrega de imagens: ver `apps/imagens/README.md` (módulo) e `apps/imagens/README_CLOUDFLARE.md` (configuração do Cloudflare). Variáveis `R2_*` e `IMAGENS_*` em `config/settings.py`.
