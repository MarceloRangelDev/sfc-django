# Sistema Faturando Certo (SFC)

Sistema de gestão de custos, precificação e fichas técnicas para negócios de alimentação/produtos, construído em **Django** (Python) com o tema visual **Vuexy**.

Este README foi escrito para servir tanto a quem programa quanto a quem só precisa instalar, configurar ou dar manutenção básica no sistema — sem assumir conhecimento prévio de Django. Sempre que possível, os termos técnicos são explicados na primeira vez que aparecem.

> Este documento foi reescrito por completo durante uma reestruturação do projeto (ver [Resumo das fases desta reestruturação](#resumo-das-fases-desta-reestruturação) no final) que tornou o sistema **multi-tenant** (multiempresa), adicionou um **sistema de módulos habilitáveis por cliente**, telas de **Papéis e Permissões**, um **log de auditoria**, o módulo de **Insumos completo** (cadastro, tabela, exportação) e traduziu toda a interface para **português do Brasil**.

---

## Sumário

1. [Visão geral](#visão-geral)
2. [Requisitos](#requisitos)
3. [Instalação passo a passo](#instalação-passo-a-passo)
4. [Estrutura de pastas](#estrutura-de-pastas)
5. [Conceitos principais](#conceitos-principais)
6. [Documentação dos módulos](#documentação-dos-módulos)
7. [Segurança](#segurança)
8. [Manutenção — tarefas do dia a dia](#manutenção--tarefas-do-dia-a-dia)
9. [Testes automatizados](#testes-automatizados)
10. [Pontos de atenção e próximos passos](#pontos-de-atenção-e-próximos-passos)
11. [Resumo das fases desta reestruturação](#resumo-das-fases-desta-reestruturação)

---

## Visão geral

O SFC permite que uma empresa (o "Cliente" do sistema, identificado por CPF ou CNPJ) cadastre usuários, insumos, categorias, e (em telas ainda a evoluir) produtos, processados/receitas, fichas técnicas e regras de precificação. Cada empresa só enxerga os próprios dados — o sistema é **multi-tenant**: várias empresas usam o mesmo sistema, mas totalmente isoladas umas das outras.

O acesso a cada tela é controlado por dois mecanismos que trabalham juntos:

- **Módulos**: uma empresa só acessa uma tela (ex: Insumos) se esse módulo estiver habilitado para ela. Isso permite, por exemplo, uma empresa do ramo de alimentação ter o módulo de Fichas Técnicas, enquanto outra empresa não precisa dele.
- **Papéis e permissões**: dentro de uma empresa, cada usuário tem um papel (Administrador, Gestor, Operador, Visualizador, ou um papel customizado) que define o que ele pode fazer (visualizar, criar, editar, excluir) em cada módulo habilitado.

---

## Requisitos

| Ferramenta | Versão usada no desenvolvimento | Observação |
|---|---|---|
| Python | 3.14 (funciona a partir de 3.12) | Linguagem em que o Django é escrito |
| Django | 5.2.5 (já fixado em `requirements.txt`) | Framework web |
| Banco de dados | SQLite (arquivo `db.sqlite3`, já incluso) | Não precisa instalar nada separado; para produção com muitos usuários simultâneos, considere migrar para PostgreSQL |
| Git | qualquer versão recente | Controle de versão |
| Node.js + npm | **opcional** | Só é necessário se for customizar o CSS/JS do tema Vuexy (pasta `src/`); os arquivos já compilados estão em `src/assets` e o sistema funciona sem rodar nenhum build de front-end |

---

## Instalação passo a passo

Os comandos abaixo assumem Windows com PowerShell (ajuste os equivalentes no Linux/Mac quando indicado).

### 1. Obter o código

```bash
git clone <url-do-repositorio>
cd sfc-django
```

### 2. Criar e ativar o ambiente virtual

Um ambiente virtual ("venv") isola as bibliotecas Python deste projeto do resto do computador.

```powershell
python -m venv .venv
.venv\Scripts\activate
```

No Linux/Mac: `python3 -m venv .venv && source .venv/bin/activate`

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Configurar o arquivo `.env`

O projeto já tem um `.env` de desenvolvimento. Se for começar do zero, crie um `.env` na raiz do projeto com, no mínimo:

```
SECRET_KEY=alguma-string-secreta-bem-grande-e-aleatoria
DEBUG=True
DJANGO_ENVIRONMENT=local
BASE_URL=http://127.0.0.1:8000

# E-mail transacional (verificação de cadastro, redefinição de senha) via Resend (https://resend.com)
RESEND_API_KEY=
MAIL_FROM_NAME=Sistema Faturando Certo
MAIL_FROM_EMAIL=

# Limites de tentativas de login e redefinição de senha (proteção contra força bruta)
LOGIN_RATE_LIMIT_ATTEMPTS=3
LOGIN_RATE_LIMIT_WINDOW=900
PASSWORD_RESET_RATE_LIMIT_ATTEMPTS=2
PASSWORD_RESET_RATE_LIMIT_WINDOW=3600
```

**Nunca** faça commit de um `.env` com uma `RESEND_API_KEY` real ou uma `SECRET_KEY` de produção — o `.gitignore` já bloqueia isso, mas fique atento se editar manualmente.

Se `RESEND_API_KEY` ficar vazia, o sistema continua funcionando normalmente para tudo, exceto o **envio real** de e-mails (verificação de cadastro / redefinição de senha) — nesse caso, o sistema mostra uma mensagem de erro amigável em vez de travar.

### 5. Rodar as migrações (criar as tabelas do banco)

```bash
python manage.py migrate
```

### 6. Criar um super-usuário (acesso ao Django Admin)

```bash
python manage.py createsuperuser
```

### 7. Aplicar os dados padrão do sistema (papéis e módulos)

```bash
python manage.py seed_acl
python manage.py seed_modulos
```

Esses comandos são **idempotentes** — podem ser rodados de novo a qualquer momento sem duplicar nada (ver [Manutenção](#manutenção--tarefas-do-dia-a-dia)).

### 8. Criar o primeiro Cliente (empresa) e vincular seu usuário a ele

> **Isso ainda não tem um "assistente de primeiro acesso"** — é preciso fazer manualmente uma vez, pelo Django Admin (`/admin/`) ou pelo shell:

```bash
python manage.py shell
```
```python
from apps.cliente.models import Cliente
from django.contrib.auth.models import User

cliente = Cliente.objects.create(
    tipo_pessoa="PJ",  # ou "PF"
    cpf_cnpj="12345678000199",  # o seu CNPJ/CPF real (só dígitos ou com máscara)
    nome_razao_social="Minha Empresa Ltda",
)

usuario = User.objects.get(username="SEU_SUPERUSUARIO")
usuario.profile.cliente = cliente
usuario.profile.save()
```

Depois disso, vá em **Módulos** (`/modulos`) logado como esse usuário e habilite os módulos que essa empresa vai usar.

### 9. Rodar o servidor de desenvolvimento

```bash
python manage.py runserver
```

Acesse **http://127.0.0.1:8000/** — você será redirecionado para a tela de login.

---

## Estrutura de pastas

```
sfc-django/
├── apps/                    # Cada subpasta é um "app" Django (um módulo do sistema)
│   ├── acl/                 # Telas de Papéis e Permissões
│   ├── auditoria/           # Log de auditoria (quem fez o quê)
│   ├── categoria/           # Categorias de insumos
│   ├── cliente/             # Empresas (CPF/CNPJ) — o "tenant" do sistema
│   ├── ficha/                     ┐
│   ├── precificacao/               │  Módulos placeholder — só a "âncora" de
│   ├── processado/                 │  permissões existe; ainda sem CRUD real
│   ├── produto/                   ┘  (ver "Pontos de atenção")
│   ├── insumo/               # Módulo de Insumos — CRUD completo (referência)
│   ├── modulo/               # Módulos habilitáveis por Cliente
│   ├── pages/                # Páginas de sistema (403/404/manutenção/em breve)
│   └── sample/                # Páginas de exemplo do tema Vuexy (Page 1/Page 2)
├── auth/                     # App de autenticação (login/cadastro/senha/perfil)
│                              # OBS: nome "auth" pode confundir com django.contrib.auth
│                              # (o do Django); por isso o app_label dele é "accounts"
├── config/                   # Configurações do projeto (settings.py, urls.py)
├── locale/                   # Catálogos de tradução (ver "Pontos de atenção")
├── templates/                # Templates HTML compartilhados (layout, auth)
├── src/                      # Assets do tema Vuexy (CSS/JS/imagens já compilados)
├── web_project/              # Utilitários do tema + decorators/mixins de segurança
│   ├── decorators.py         # screen_required / module_required (proteção de tela)
│   └── mixins.py             # ClienteScopedMixin (isolamento multi-tenant)
├── db.sqlite3                # Banco de dados (não versionado no Git)
├── requirements.txt          # Dependências Python
├── Group-ACL.md              # Nota técnica original sobre o desenho do ACL
└── manage.py                 # Ponto de entrada dos comandos Django
```

---

## Conceitos principais

### Cliente (tenant)

`apps/cliente/models.py::Cliente` — representa uma empresa ou pessoa física, identificada por CPF ou CNPJ (validado matematicamente, não só por máscara — ver [Segurança](#segurança)). Um Cliente pode ter **N usuários** vinculados (`auth/models.py::Profile.cliente`); cada usuário pertence a **exatamente um** Cliente.

### Módulo

`apps/modulo/models.py::Modulo` — uma tela de negócio (Insumos, Categorias, Produto...). `ClienteModulo` define se aquele módulo está habilitado para um Cliente específico. Gerenciado na tela **Módulos** (`/modulos`).

### Papel (Group) e Permissão

O sistema **reaproveita 100% o mecanismo nativo do Django** (`django.contrib.auth.models.Group`/`Permission`) — não existe uma tabela de "papéis" própria. Um Papel é um `Group`; uma Permissão é uma `Permission`. Gerenciados nas telas **Papéis** e **Permissões** (`/papeis`, `/permissoes`). Ver `Group-ACL.md` para o desenho original e a seção [Segurança](#segurança) abaixo para o funcionamento atual.

### Guarda de sessão

Toda tela de negócio exige login. O mecanismo é sempre no **backend** (nunca só escondendo um botão no HTML): `web_project/decorators.py::screen_required`/`module_required`, aplicados em cada `urls.py`.

---

## Documentação dos módulos

### Autenticação (`auth/`)

| | |
|---|---|
| **Objetivo** | Login, cadastro, recuperação/redefinição de senha, verificação de e-mail, perfil do usuário |
| **Tabelas** | `accounts_profile` (via model `Profile`) |
| **Telas** | `/login/`, `/register/`, `/forgot_password/`, `/reset_password/<token>/`, `/verify_email/`, `/profile/` |
| **Observação** | Todo o fluxo está em português do Brasil. Cadastro novo entra automaticamente no papel **Visualizador** |

### Clientes (`apps/cliente/`)

| | |
|---|---|
| **Objetivo** | Cadastrar as empresas (tenants) e vincular usuários a elas |
| **Tabela** | `cliente_cliente` |
| **Tela** | `/clientes` (listar/criar/editar + vincular/desvincular usuários) |
| **Permissões** | `cliente.view_cliente`, `add_cliente`, `change_cliente`, `delete_cliente` — concedidas **só ao papel Administrador** por padrão (`auth/management/commands/seed_acl.py`) |
| **Dependências** | Nenhuma |

### Módulos (`apps/modulo/`)

| | |
|---|---|
| **Objetivo** | Definir quais telas existem no sistema e habilitá-las por Cliente |
| **Tabelas** | `modulo_modulo`, `modulo_clientemodulo` |
| **Tela** | `/modulos` |
| **Permissões** | `modulo.change_clientemodulo` — só Administrador |
| **Comando** | `python manage.py seed_modulos` |

### Papéis e Permissões (`apps/acl/`)

| | |
|---|---|
| **Objetivo** | Gerenciar `Group` (papéis) e ver o catálogo de `Permission` |
| **Tabelas** | Nenhuma própria — usa `auth_group`/`auth_permission` (nativas do Django) |
| **Telas** | `/papeis`, `/permissoes` |
| **Permissões** | `auth.view_group`/`add_group`/`change_group`/`delete_group`, `auth.view_permission` — só Administrador |
| **Observação** | A matriz de permissões atribuível por esta tela **não inclui** gerenciar papéis/clientes/módulos — de propósito, para um papel comum nunca poder se autoconceder acesso administrativo (ver `apps/acl/permissions_matrix.py`) |

### Auditoria (`apps/auditoria/`)

| | |
|---|---|
| **Objetivo** | Registrar quem fez o quê, quando, em qual cliente/módulo e com que resultado |
| **Tabela** | `auditoria_registroauditoria` |
| **Onde ver** | Django Admin (`/admin/auditoria/registroauditoria/`) — somente leitura |
| **O que já é registrado** | Login (sucesso/falha), logout, criar/editar/excluir/exportar Insumo |
| **Como estender** | Ver [Manutenção](#manutenção--tarefas-do-dia-a-dia) |

### Insumos (`apps/insumo/`) — módulo de referência com CRUD completo

| | |
|---|---|
| **Objetivo** | Cadastro dos insumos usados nas receitas/produtos de cada Cliente |
| **Tabela** | `insumo_insumo` |
| **Campos** | nome, categoria (FK), unidade de medida, quantidade e preço (`DecimalField`, nunca `float`), ativo (exclusão lógica), cliente (tenant), timestamps |
| **Tela** | `/insumo` — tabela com paginação (10/25/50/100), pesquisa por nome/categoria, ordenação por coluna, exportação Excel/PDF; `/insumo/novo`, `/insumo/<id>/editar`, `/insumo/<id>/excluir` |
| **Permissões** | `insumo.view_insumo`/`add_insumo`/`change_insumo`/`delete_insumo` |
| **Dependências** | `apps.categoria` (FK), `openpyxl` (Excel), `reportlab` (PDF) |
| **Exclusão** | Lógica (`ativo=False`) — o registro nunca é apagado de verdade |

### Categorias (`apps/categoria/`)

| | |
|---|---|
| **Objetivo** | Organizar os Insumos de um Cliente |
| **Tabela** | `categoria_categoria` |
| **Tela** | `/categoria` (listar/criar/editar) |
| **Permissões** | `categoria.view_categoria`/`add_categoria`/`change_categoria` |
| **Observação** | Ganhou o campo `cliente` na Fase 5 (antes não existia isolamento multi-tenant nesta tabela) |

### Produto, Processado, Ficha Técnica, Precificação (`apps/produto`, `apps/processado`, `apps/ficha`, `apps/precificacao`)

Ainda são modelos **"âncora"** — só têm o campo `nome` e existem para gerar as permissões nativas (`view_/add_/change_/delete_`) usadas pelo ACL e pelo menu. **Não têm CRUD real, nem campo `cliente`, nem tela além do placeholder.** Servem de próximo passo natural, seguindo exatamente o mesmo roteiro do módulo de Insumos (ver [Manutenção](#manutenção--tarefas-do-dia-a-dia) → "Como criar um novo módulo completo").

---

## Segurança

### Autenticação e sessão

- Toda tela de negócio exige login, verificado no **backend** (`screen_required`/`module_required` em cada `urls.py`) — nunca apenas escondendo um link no menu.
- Sessões expiram em 1 hora (`SESSION_COOKIE_AGE`), cookies com `Secure`, `HttpOnly` e `SameSite=Lax`.
- Login e recuperação de senha têm limite de tentativas (`auth/ratelimit.py`), por IP + usuário/e-mail informado.
- Token de redefinição de senha expira em 24h (`Profile.forget_password_token_expiration`) — **corrigido nesta reestruturação**: antes, o token nunca expirava de verdade.

### Autorização (papéis e permissões)

- 100% em cima do sistema nativo do Django (`Group`/`Permission`) — ver `Group-ACL.md` e a seção [Papéis e Permissões](#papéis-e-permissões-appsacl) acima.
- A tela de Papéis **nunca aceita**, ao salvar, um id de permissão fora da matriz conhecida (testado enviando manualmente o id de uma permissão administrativa via requisição direta — foi ignorado).

### Isolamento multi-tenant

- Toda tela de negócio usa `web_project/mixins.py::ClienteScopedMixin`, que filtra **sempre** pelo Cliente do usuário autenticado (nunca por um valor vindo do formulário/POST/querystring).
- Testado manualmente e via testes automatizados: um usuário do Cliente B **não vê, não edita e não exclui** um registro do Cliente A (retorna 404, não um erro que revele a existência do dado).
- Uma Categoria só pode ser usada por Insumos do mesmo Cliente — validado tanto pelo `queryset` do formulário quanto por uma checagem explícita (`InsumoForm.clean_categoria`), caso alguém tente forçar via requisição direta.

### CSRF, XSS, SQL Injection, IDOR

- CSRF: proteção nativa do Django, nunca desabilitada (todo formulário usa `{% csrf_token %}`).
- XSS: nenhum template usa `|safe` ou `{% autoescape off %}` sobre dado vindo do usuário.
- SQL Injection: nenhuma query usa SQL cru/concatenado — tudo via ORM do Django. A ordenação da tabela de Insumos usa uma **whitelist** de colunas (`COLUNAS_ORDENAVEIS` em `apps/insumo/views.py`) — o valor de `?sort=` nunca é repassado direto para `order_by()`.
- IDOR (acessar/alterar um registro de outro dono trocando um id na URL): protegido em toda tela de negócio pelo `ClienteScopedMixin`, testado manualmente e por testes automatizados.
- Mass assignment: todo formulário usa uma lista explícita de campos (`ModelForm.Meta.fields`) — nunca `Model(**request.POST)`.

### Valores monetários e de quantidade

- `DecimalField` em vez de `float` (Insumo.quantidade, Insumo.preco) — evita erros de arredondamento de ponto flutuante em valores financeiros.
- Negativos bloqueados (`MinValueValidator(0)`), tanto no formulário quanto no model (duas camadas).
- `NaN`/`Infinity` bloqueados — checagem nativa do próprio Django (`forms.DecimalField.validate()`), confirmada com teste automatizado e com requisição direta (sem passar pelo formulário/JS).
- Aceita formato brasileiro de número ("1.234,56" ou "R$ 1.234,56") e normaliza antes de validar (`apps/insumo/forms.py::normalizar_valor_monetario`).

### Exportação (Excel/PDF)

- Sempre respeita Cliente + filtro/pesquisa/ordenação atuais da tela — nunca exporta a tabela inteira do sistema.
- Proteção contra **Formula/CSV Injection**: texto que comece com `=`, `+`, `-` ou `@` é prefixado com aspas simples antes de ir para a célula do Excel (testado com um insumo cujo nome era uma fórmula maliciosa).

### Auditoria

- Ver [seção do módulo](#auditoria-appsauditoria) acima. **Limitação conhecida**: uma tentativa de login com um **usuário inexistente** não gera registro de auditoria (só falha de senha para um usuário existente) — a tela de login retorna antes de chamar `authenticate()` nesse caso. Ver [Pontos de atenção](#pontos-de-atenção-e-próximos-passos).

### Bugs de segurança/corretude corrigidos nesta reestruturação

1. **Home page sem proteção** (`apps/sample`) — qualquer visitante anônimo acessava o dashboard.
2. **Token de redefinição de senha nunca expirava** (faltava o campo no model).
3. **Credenciais de demonstração fixas** no formulário de login (`admin@demo.com`/`Pwd_1234`).
4. **Páginas de erro sempre retornavam HTTP 200** (ou 405 em requisições POST) em vez do status correto (401/403/404/500) — `web_project/views.py::SystemView` só usava `self.status` para exibir o número na página, nunca como status HTTP de verdade, e só tinha método GET. **Este era um bug pré-existente que afetava o projeto inteiro** (todo `handler403`/`404`/etc.), descoberto só ao escrever os testes automatizados da Fase 6.
5. Diversos bugs pontuais no fluxo de verificação de e-mail e redefinição de senha (ver commits da Fase 1).

---

## Manutenção — tarefas do dia a dia

### Como criar um novo módulo completo (seguindo o padrão de Insumos)

O módulo de **Insumos** (`apps/insumo/`) é a referência a copiar para os próximos módulos (Produto, Processado, Ficha, Precificação):

1. No `models.py` do app, adicione os campos reais (sempre com `cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, ...)`).
2. Se o model referenciar outro (ex: uma FK para Categoria), garanta que o relacionado também tenha `cliente` e valide, no formulário, que os dois pertencem ao mesmo Cliente (ver `InsumoForm.clean_categoria`).
3. Crie o `forms.py` com um `ModelForm`, recebendo `cliente` no `__init__` para filtrar querysets de FKs e validar duplicidade só dentro do próprio Cliente.
4. Nas views, use `web_project.mixins.ClienteScopedMixin` em toda `ListView`/`CreateView`/`UpdateView`.
5. No `urls.py`, troque `screen_required` por `web_project.decorators.module_required("<slug_do_modulo>", "<app>.<acao>_<model>")`.
6. Rode `python manage.py makemigrations <app> && python manage.py migrate`.
7. Rode `python manage.py seed_acl` (para as novas permissões entrarem nos papéis padrão) e `python manage.py seed_modulos` (se for um módulo novo, adicione-o em `MODULOS_PADRAO` em `apps/modulo/management/commands/seed_modulos.py`).
8. Escreva testes em `apps/<app>/tests.py` cobrindo: CRUD, validação de valores, e isolamento multi-tenant (copie a estrutura de `apps/insumo/tests.py`).

### Como criar uma nova permissão

Permissões `view_`/`add_`/`change_`/`delete_` são criadas **automaticamente pelo Django** para cada model, na primeira `migrate` depois de `makemigrations`. Não é preciso criá-las manualmente. Para permissões que não são as 4 padrão, use `class Meta: permissions = [...]` no model (ver documentação do Django sobre "custom permissions").

### Como criar/editar um papel

Pela interface: `/papeis` → "Novo papel" → marcar as permissões na matriz → Salvar. Isso só chama `Group.objects.create()`/`group.permissions.set(...)` por baixo dos panos.

### Como habilitar/desabilitar um módulo para um Cliente

Pela interface: `/modulos` → selecionar o Cliente → marcar/desmarcar os módulos → Salvar.

### Como rodar as migrações

```bash
python manage.py makemigrations   # depois de alterar algum models.py
python manage.py migrate          # aplica no banco
```

**Nunca** apague uma migration já aplicada em produção, nem edite uma migration antiga depois que ela já rodou em algum ambiente — crie sempre uma nova.

### Como rodar os comandos de seed

```bash
python manage.py seed_acl       # papéis padrão (Administrador/Gestor/Operador/Visualizador)
python manage.py seed_modulos   # módulos padrão + mantém habilitados para clientes existentes
```

Ambos são idempotentes (podem rodar quantas vezes forem necessárias).

### Como rodar os testes

```bash
python manage.py test
```

Ver [Testes automatizados](#testes-automatizados) abaixo.

### Como atualizar dependências

```bash
pip install -r requirements.txt --upgrade
pip freeze > requirements.txt   # revise o diff antes de commitar
```

### Como ver os logs de auditoria

Django Admin → **Auditoria** → **Registros de auditoria** (somente leitura, com filtro por ação/módulo/resultado/cliente e busca por usuário).

### Como adicionar uma nova ação ao log de auditoria

```python
from apps.auditoria.utils import registrar as registrar_auditoria

registrar_auditoria(
    request, acao="criar", modulo="produto", registro_id=objeto.pk,
    descricao=f'Produto "{objeto.nome}" cadastrado.',
)
```

Chame isso a partir de qualquer view, logo após a ação (ver `apps/insumo/views.py` para os 4 pontos onde isso é usado: criar, editar, excluir, exportar).

---

## Testes automatizados

```bash
python manage.py test
```

Cobertura atual (52 testes): autenticação (login/logout/guarda de sessão/cadastro), validação de CPF/CNPJ, gate de módulo habilitado/desabilitado, CRUD e validação de valores do Insumo, pesquisa/ordenação/paginação/exportação, e isolamento multi-tenant (tentativas de acesso cruzado entre clientes).

> **Importante para quem for adicionar um teste novo**: sempre passe um `email` explícito ao criar um `User` de teste (`User.objects.create_user(username=..., email=f"{username}@teste.local", password=...)`) — o campo `Profile.email` é único, e vários usuários de teste sem e-mail colidem entre si (`email=""` para todos).

---

## Pontos de atenção e próximos passos

- **Produto, Processado, Ficha Técnica e Precificação** ainda são placeholders — o próximo trabalho natural é repetir para eles o que foi feito em Insumos (ver [Manutenção](#manutenção--tarefas-do-dia-a-dia)).
- **Tabela órfã `transactions_transaction`**: existem 7 linhas no banco de dados de um app chamado `transactions` que não existe mais no código-fonte (resíduo de uma versão anterior do projeto, antes desta reestruturação). Foi deixada intacta — nenhuma migração a referencia, então `migrate` não a toca — mas alguém deveria decidir explicitamente se essa tabela deve ser restaurada, migrada ou removida.
- **Tradução para outros idiomas (en/fr/de/ar)**: o `LANGUAGE_CODE` padrão agora é `pt-br`, e as mensagens nativas do Django (validação de formulário etc.) já vêm em pt-BR automaticamente, pois o próprio Django inclui essa tradução. Porém, o texto pt-BR escrito nos templates/views deste projeto **não tem catálogo de tradução gerado** (`locale/pt_BR/` está vazio) — isso não afeta o funcionamento em português, mas é necessário rodar `django-admin makemessages`/`compilemessages` (requer as ferramentas GNU gettext instaladas, que não vêm por padrão no Windows) se algum dia for preciso oferecer o sistema em outro idioma.
- **Sem assistente de primeiro acesso**: criar o primeiro Cliente e vinculá-lo a um usuário precisa ser feito manualmente (Django Admin ou shell) — ver [Instalação, passo 8](#8-criar-o-primeiro-cliente-empresa-e-vincular-seu-usuário-a-ele).
- **Auditoria de login com usuário inexistente**: não é registrada (só login com senha errada para um usuário que existe) — limitação do fluxo atual de login, documentada em [Segurança](#segurança).
- **Tabela de Insumos**: a paginação/pesquisa/ordenação são feitas pelo servidor com HTML simples (não usa a biblioteca DataTables já presente no tema Vuexy) — decisão consciente para reduzir a superfície de código JavaScript nesta etapa; migrar para DataTables com um endpoint JSON próprio é possível como evolução futura, sem mudar o modelo de dados.

---

## Resumo das fases desta reestruturação

Cada fase abaixo é um commit Git separado (`git log --oneline`), podendo ser revertida isoladamente se necessário.

| Fase | O que foi feito | Principais arquivos/apps novos |
|---|---|---|
| 0 | `git init` + commit inicial (rede de segurança antes de qualquer mudança) | — |
| 1 | Fechou a guarda de sessão (home pública), corrigiu bugs reais do fluxo de auth (token de senha nunca expirava, crash em token inválido, credenciais de demo expostas), traduziu login/cadastro/recuperação de senha/navbar para pt-BR | `auth/*`, `templates/auth/*.html` |
| 2 | Cliente multi-tenant com CPF/CNPJ validado matematicamente; N usuários por Cliente | `apps/cliente/` (novo) |
| 3 | Módulos habilitáveis por Cliente, bloqueados de verdade no backend (não só escondidos do menu) | `apps/modulo/` (novo) |
| 4 | Telas de Papéis e Permissões (estilo Vuexy), em cima do `Group`/`Permission` nativo | `apps/acl/` (novo) |
| 5 | Módulo de Insumos completo: campos reais, tabela com paginação/pesquisa/ordenação, CRUD, exportação Excel/PDF; Categoria ganhou isolamento por Cliente | `apps/insumo/*`, `apps/categoria/*` |
| 6 | Log de auditoria; suíte de testes automatizados (52 testes); correção de 2 bugs reais pré-existentes descobertos pelos testes (`apps/__init__.py` faltando fazia o Django nunca rodar nenhum teste do projeto; `SystemView` nunca retornava o status HTTP correto) | `apps/auditoria/` (novo), `*/tests.py` |
| 7 | Este README | `README.md` |

**Funcionalidades preservadas** (não alteradas nesta reestruturação): tema visual Vuexy, menu lateral dinâmico por JSON, e-mails via Resend, rate limit de login/recuperação de senha, segmento de trabalho (Serviço/Produto) do perfil do usuário.

**Testes executados**: `python manage.py test` (52 testes, todos passando) e verificação manual completa de cada fluxo no navegador (login/logout, CPF/CNPJ inválido/duplicado, módulo habilitado/desabilitado, criar papel e tentar burlar a matriz de permissões, CRUD de Insumo com valores inválidos/negativos/NaN/Infinity, exportação Excel/PDF com proteção contra Formula Injection, e tentativas de acesso cruzado entre dois Clientes diferentes).
