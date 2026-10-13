# ACL, Rate Limit e Segmento de Trabalho

Este documento descreve como o controle de acesso (ACL), o rate limit de
login/recuperação de senha e o segmento de trabalho do perfil foram
implementados no projeto.

## 1. ACL (Grupos + Permissions nativas do Django)

O ACL usa o sistema nativo do Django: `django.contrib.auth.models.Group` e
`Permission`. Cada tela de negócio (Insumo, Produto, Processado, Categoria,
Ficha Técnica, Precificação) tem um model "âncora" simples em
`apps/<app>/models.py`, que faz o Django gerar automaticamente 4 permissões:

- `<app>.view_<model>` — visualizar a tela
- `<app>.add_<model>` — criar
- `<app>.change_<model>` — editar
- `<app>.delete_<model>` — excluir

### Menu dinâmico

> **Atualizado:** o menu agora é montado a partir da tabela de módulos
> (`web_project/menu.py`); o JSON guarda apenas itens estáticos. O texto
> abaixo descreve o desenho original.

`templates/layout/partials/menu/vertical/json/vertical_menu.json` tinha um
campo `"permission"` em cada item de tela (ex: `"insumo.view_insumo"`). Os
templates do menu (`menu_item_template.html`, `menu_collapsible_template.html`,
`menu_header_template.html`) só mostram o item se
`request.user|has_permission:item.permission` for verdadeiro (filtro definido
em `web_project/template_tags/theme.py`, que chama `user.has_perm(...)`).
Um item sem `"permission"` fica visível para qualquer usuário autenticado.

### Protegendo as telas (views)

Além de esconder do menu, cada `urls.py` das telas de negócio usa o helper
`web_project/decorators.py::screen_required`, que exige login **e** a
permissão de visualização:

```python
from web_project.decorators import screen_required

path("insumo", screen_required("insumo.view_insumo")(InsumoView.as_view(...)), name="insumo"),
```

Quem não tem a permissão é redirecionado para a página "Não autorizado"
(`pages_misc_not_authorized.html`, via `handler403` em `config/urls.py`).

Quando as telas de CRUD forem implementadas, use o mesmo padrão nas views/
formulários de criar, editar e excluir, trocando a permissão:
`<app>.add_<model>`, `<app>.change_<model>`, `<app>.delete_<model>`.

### Grupos padrão

O comando `python manage.py seed_acl` cria/atualiza 4 grupos aplicados a
todas as telas de negócio:

| Grupo          | Visualizar | Criar | Editar | Excluir |
|----------------|:----------:|:-----:|:------:|:-------:|
| Administrador  | ✅ | ✅ | ✅ | ✅ |
| Gestor         | ✅ | ✅ | ✅ | ❌ |
| Operador       | ✅ | ✅ | ❌ | ❌ |
| Visualizador   | ✅ | ❌ | ❌ | ❌ |

O grupo `client`, usado como padrão para quem se cadastra pelo `/register/`
(ver `auth/register/views.py`), continua existindo separadamente e hoje não
tem nenhuma permissão de tela atribuída — ajuste em Django Admin
(`/admin/auth/group/`) ou no comando `seed_acl.py` conforme a regra de
negócio desejada para novos cadastros.

Ajuste as permissões de cada grupo em `/admin/auth/group/`, ou edite
`GROUPS`/`SCREEN_APPS` em `auth/management/commands/seed_acl.py` e rode o
comando novamente (é idempotente).

### Simulação de acesso (tela ADM)

A ideia de "simular o acesso do usuário" citada no projeto ainda não tem
tela própria. Com esse modelo baseado em `Group`/`Permission` nativos, dá
para implementar depois usando `django.contrib.auth.views` (`login_as` /
"hijack") ou checando `user.has_perms([...])` de um usuário-alvo sem trocar
de sessão.

## 2. Rate limit (login e recuperação de senha)

Implementado em `auth/ratelimit.py`, usando o cache do Django
(`settings.CACHES`, LocMemCache por padrão — trocar por Redis em produção
com mais de um processo/servidor).

- **Login** (`auth/login/views.py`): até `LOGIN_RATE_LIMIT_ATTEMPTS` (padrão
  3) tentativas por `LOGIN_RATE_LIMIT_WINDOW` segundos (padrão 900 = 15 min),
  contadas por IP + usuário/e-mail informado. Ao estourar, mostra mensagem
  pedindo para tentar novamente em alguns minutos. O contador é limpo no
  login bem-sucedido.
- **Recuperação de senha** (`auth/forgot_password/views.py`): até
  `PASSWORD_RESET_RATE_LIMIT_ATTEMPTS` (padrão 2) solicitações por
  `PASSWORD_RESET_RATE_LIMIT_WINDOW` segundos (padrão 3600 = 1h), contadas
  por IP + e-mail informado. A tentativa é contada mesmo se o e-mail não
  existir no sistema, para não abrir brecha de enumeração de e-mails via
  rate limit.

Os limites são configuráveis via `.env` (`LOGIN_RATE_LIMIT_ATTEMPTS`,
`LOGIN_RATE_LIMIT_WINDOW`, `PASSWORD_RESET_RATE_LIMIT_ATTEMPTS`,
`PASSWORD_RESET_RATE_LIMIT_WINDOW`).

## 3. E-mails via Resend

Todos os e-mails transacionais (verificação de conta, redefinição de senha)
passaram a ser enviados pela API do Resend (https://resend.com), através de
um backend de e-mail customizado (`config/email_backend.py`), então todo o
código que usa `django.core.mail` continua funcionando normalmente.

Configuração no `.env`:

```
RESEND_API_KEY=...
MAIL_FROM_NAME=Sistema Faturando Certo
MAIL_FROM_EMAIL=naoresponda@sistemafaturandocerto.com
MAIL_REPLY_TO=naoresponda@sistemafaturandocerto.com
```

Antes de usar em produção, é preciso verificar o domínio do remetente
(`MAIL_FROM_EMAIL`) no painel do Resend.

## 4. Segmento de trabalho

> **Atualizado:** o segmento agora pertence ao **Cliente** (`Cliente.segmento`
> -> `modulo.Segmento`), não mais ao usuário. O campo
> `Profile.segmento_trabalho` foi removido (a migration `cliente.0006` copiou
> o valor para o cliente). O tipo de precificação (`servico` / `produto` /
> `ambos`) vive em `Segmento.tipo_precificacao`. Veja `apps/modulo/README.md`.

Outras partes do sistema podem consultar o segmento do usuário logado via:

- `request.user.profile.segmento` (herdado do cliente)
- `request.user.profile.precifica_servico` / `precifica_produto` (booleanos)
- Nos templates: filtros `is_segmento_servico` / `is_segmento_produto`
  (`web_project/template_tags/theme.py`)

As regras de precificação específicas de cada segmento (o "como calcular")
ainda não foram desenhadas — isso fica para quando a tela de Precificação
for implementada de fato (hoje é só uma tela placeholder).

---

## Sugestões originais de nomenclatura de grupos (referência)

Aqui está uma sugestão de nomes para grupos de permissão (ACL) organizados
por níveis de acesso e por departamentos, ideais para manter o sistema SaaS
organizado e escalável — mantido aqui como referência para quando o sistema
crescer além dos 4 grupos padrão listados acima:

### 📊 Por Nível de Responsabilidade (Estrutura Hierárquica)
 - **SuperAdmin / Root:** Acesso total e irrestrito ao sistema, configurações globais e infraestrutura.
 - **AccountOwner / BillingAdmin:** Gerencia assinaturas, dados de pagamento, faturamento e planos.
 - **WorkspaceAdmin / OrgAdmin:** Administra uma organização ou espaço de trabalho específico, gerenciando usuários.
 - **Manager / Lead:** Permissões de edição avançadas, aprovação de fluxos e visualização de relatórios do time.
 - **Contributor / Editor:** Usuário padrão que cria, edita e deleta conteúdos do seu próprio escopo de trabalho.
 - **Viewer / Guest:** Acesso estritamente para leitura (Read-Only), sem permissão para alterações.
---
### 🏢 Por Departamentos (Estrutura Operacional)
- **HR_Admin / People_Ops:** Acesso a dados de funcionários, contratações e informações sensíveis de RH.
- **Finance_Operator:** Visualiza e edita fluxos de caixa, notas fiscais e relatórios financeiros.
- **Sales_Representative:** Permissão para gerenciar leads, funil de vendas e dados de clientes (CRM).
- **Support_Agent / Helpdesk:** Acesso a chamados, histórico de atendimento e dados básicos do cliente para suporte.
- **Marketing_Manager:** Controla campanhas, integrações de anúncios e ferramentas de conteúdo.
- **Dev_Ops / Eng_Admin:** Permissões técnicas para integrações, chaves de API e webhooks.
---
### 🔒 Grupos Especiais de Segurança e Auditoria
- **Auditor / Compliance:** Acesso de leitura a logs de sistema, históricos de alteração e dados de segurança.
- **Data_Analyst / BI_Viewer:** Permissão focada em exportação de relatórios e conexão com ferramentas de Business Intelligence.

---

## Atualização (Fases 3 a 8)

Novas telas/recursos protegidos pelo mesmo mecanismo (permissões nativas do Django) e concedidos **somente ao grupo Administrador** por `seed_acl`:

| Recurso | Permissões usadas |
|---|---|
| Medidas (`/medidas`) | `medida.view/add/change/delete_medida` |
| Usuários (`/usuarios`) | `auth.view_user`, `auth.add_user`, `auth.change_user` (não existe exclusão física) |
| Auditoria (`/auditoria`) | `auditoria.view_registroauditoria` (a única que existe: o log é imutável) |

Além disso:

- **Perfis por cliente:** `Cliente.perfis` define quais papéis podem ser atribuídos aos usuários daquele cliente; o `UsuarioForm` (e a API) validam isso no servidor.
- **Escopo:** quem não é administrador global (superusuário ou grupo "Administrador") só enxerga usuários e auditoria do próprio cliente (`web_project/acesso.py`).
- **API:** a classe `apps/api/permissions.py::PermissaoDoSistema` aplica as mesmas permissões e a mesma checagem de módulo habilitado (`modulo_slug`) às rotas `/api/v1/`.

Veja o README (seções "ACL", "API" e "Segurança") para o quadro completo.
