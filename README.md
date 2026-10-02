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
18. [Medidas](#medidas)
19. [Regras de negócio](#regras-de-negócio)
20. [Máscaras](#máscaras)
21. [Indicadores](#indicadores)
22. [Encoding (UTF-8)](#encoding-utf-8)
23. [Segurança](#segurança)
24. [Testes](#testes)
25. [Manutenção](#manutenção)
26. [Decisões técnicas](#decisões-técnicas)
27. [Pendências e pontos de atenção](#pendências-e-pontos-de-atenção)

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
python manage.py seed_modulos    # cria os módulos e habilita nos clientes já existentes
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
│   ├── categoria/              # Categorias (usadas pelos produtos)
│   ├── cliente/                # Clientes (empresas/tenants) e seus perfis disponíveis
│   ├── insumo/                 # Insumos (cálculo de preço por KG/L/UN)
│   ├── medida/                 # Unidades de medida (tabela `medidas`) + prévia de cálculo
│   ├── modulo/                 # Módulos habilitáveis por cliente
│   ├── usuario/                # Cadastro de usuários (cliente + perfil)
│   ├── produto/, processado/,  # "Âncoras" de permissão: ainda sem tela/CRUD (veja Pendências)
│   │   ficha/, precificacao/
│   └── pages/, sample/         # Páginas de sistema e exemplos do tema
├── auth/                       # Login, cadastro, senha, perfil; services.py compartilhado com a API
├── config/                     # settings.py, urls.py
├── web_project/
│   ├── regras/                 # REGRAS DE NEGÓCIO globais (dinheiro, conversão, extenso, indicadores...)
│   ├── decorators.py           # screen_required / module_required (proteção de telas)
│   ├── mixins.py               # ClienteScopedMixin (isolamento por cliente)
│   ├── acesso.py               # administrador_global(), cliente_do_usuario()
│   └── forms.py                # Campos de formulário reutilizáveis (DecimalBRField, MoedaCentavosField)
├── src/assets/js/              # mascaras.js, calculo-medida.js, template-customizer-pt-br.js
├── templates/                  # Layout, menu (vertical_menu.json), telas de auth
├── nginx/, Dockerfile, docker-compose.yml, gunicorn-cfg.py
└── manage.py
```

---

## Autenticação

**Autenticação** = provar quem você é.

- **Web:** login com usuário **ou** e-mail e senha (`/login/`). A sessão dura 1 hora, é guardada no banco e o cookie só trafega por HTTPS em produção. O login tem **rate limit** (limite de tentativas): após 3 falhas seguidas, bloqueia por 15 minutos.
- **Cadastro:** `/register/` cria a conta no perfil de menor privilégio (Visualizador), **sem cliente** (ou seja, sem acesso a nenhum módulo) e envia e-mail de verificação. Um administrador vincula o cliente e o perfil depois.
- **Senhas:** nunca são guardadas em texto puro (hash PBKDF2 nativo do Django) e passam pelos validadores do Django (tamanho mínimo, não ser comum, não ser só números, não parecer com o usuário) no cadastro, na redefinição e na API.
- **Recuperação de senha:** "Esqueci minha senha" envia um link com token de **uso único** válido por 24 horas. A resposta é igual exista ou não o e-mail (não revela quais contas existem).
- **API:** token `Bearer` (veja [API](#api)).

A lógica de cadastro/redefinição fica em `auth/services.py` e é **a mesma** para web e API.

## ACL: Cliente, Perfil, Usuário e Permissão

**ACL** (Access Control List) é a lista de quem pode fazer o quê. A cadeia do sistema é:

```
Usuário  →  Cliente  →  Perfil  →  Permissões
(auth_user)  (empresa)   (papel)    (visualizar/criar/editar/excluir por módulo)
```

| Conceito | O que é | Onde |
|---|---|---|
| **Usuário** | Pessoa que loga (`auth_user` do Django + `Profile`) | tela **Usuários** |
| **Cliente** | Empresa (CPF/CNPJ). Isola os dados: ninguém vê dados de outro cliente | tela **Clientes** |
| **Perfil** | Papel (`Group` do Django): Administrador, Gestor, Operador, Visualizador ou customizado | tela **Papéis** |
| **Permissão** | Ação em um módulo (`insumo.view_insumo`, `insumo.add_insumo`...) | tela **Permissões** (catálogo) |

Regras importantes:

- Cada **Cliente** tem uma lista de **perfis disponíveis** (tela Clientes). Ao criar um usuário, o perfil escolhido **precisa estar nessa lista** (validado no servidor).
- Quem **não** é administrador global (superusuário ou perfil "Administrador") só enxerga e gerencia usuários, insumos e auditoria do **próprio cliente**. Isso é filtrado no backend: trocar o `id` na URL devolve 404.
- Perfis padrão (criados por `seed_acl`): **Administrador** (tudo, inclusive Clientes, Módulos, Medidas, Usuários, Auditoria, Papéis), **Gestor** (ver/criar/editar), **Operador** (ver/criar), **Visualizador** (só ver).
- Permissões sensíveis (gerenciar papéis/usuários) **não** podem ser atribuídas pela tela de Papéis — só o grupo Administrador as tem.
- Usuários **nunca são excluídos**, só inativados (preserva histórico e auditoria).

Detalhes técnicos do desenho original em `Group-ACL.md`.

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

**Não existem** endpoints de produtos, processados, fichas técnicas e precificação porque esses módulos ainda não têm dados nem telas (são só "âncoras" de permissão). Quando forem implementados, siga o passo a passo de [Manutenção](#manutenção).

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
5. Registre o módulo em `seed_modulos`, a permissão em `seed_acl` (`SCREEN_APPS`) e em `apps/acl/permissions_matrix.py`, e o item de menu em `templates/layout/partials/menu/vertical/json/vertical_menu.json`.
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

## Pendências e pontos de atenção

1. **Produto, Processado, Ficha Técnica e Precificação** continuam sendo "âncoras" de permissão (model com só `nome`), sem telas, API ou campos monetários. As regras de indicadores, conversão e preço por unidade já estão prontas para elas, mas não foram ligadas porque não há onde usá-las.
2. **PostgreSQL:** a configuração por variáveis de ambiente está pronta, mas a suíte de testes só foi executada em SQLite. Rode `python manage.py test` apontando para um PostgreSQL de teste antes de ir para produção.
3. **Cache/throttling em memória:** o rate limit e o *throttling* usam o cache do Django (`LocMemCache`, por processo). Com vários processos/servidores em produção, configure um cache compartilhado (Redis) em `CACHES`.
4. **Um token por usuário:** logar em um segundo aparelho desconecta o primeiro (decisão de simplicidade; evoluir para um token por dispositivo se necessário).
5. **CPF/CNPJ de usuário:** o `auth_user` não tem campo de documento (só o Cliente tem); por isso a validação de CPF/CNPJ se aplica ao cadastro de Clientes.
6. **Login web não exige e-mail verificado** (comportamento anterior mantido; o e-mail de verificação existe, mas a verificação não bloqueia o acesso).
7. **Permissões antigas de auditoria** (`add/change/delete_registroauditoria`) continuam existindo no banco de quem já tinha o sistema; são inofensivas (a tela só usa `view`).
8. **Interface:** a verificação visual das telas foi feita manualmente no navegador (insumos, medidas, usuários, filtro de perfil por cliente); não há testes automatizados de interface (clicar em botões).
9. **Valores de referência de cozinha** (copo americano 200 ml, xícara 240 ml, colher de café 2 ml) podem variar por região — ajuste na tela de Medidas conforme a sua realidade (só se a medida ainda não estiver em uso).
10. **Não foi feito teste de carga** (muitos usuários simultâneos).
