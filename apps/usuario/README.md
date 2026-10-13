# apps/usuario — Usuários vinculados a Cliente e Perfil

## Objetivo

Cadastro e manutenção de usuários (`auth_user` + `accounts_profile`) sempre ligados a **um Cliente** e **um Perfil** (grupo). Usuários nunca são apagados, apenas inativados (preserva auditoria).

## Estrutura

| Arquivo | Papel |
|---|---|
| `forms.py` | `UsuarioForm`: valida cliente, perfil liberado, regras do Admin, senha e e-mail único. Reusado pela API. |
| `services.py` | `salvar_usuario()` e `alternar_ativo()` (transação + auditoria). `foto_usuario()` para o antes/depois. |
| `views.py` | Lista com filtros/paginação, formulário único (novo/editar), ativar/inativar. |
| `urls.py` | `usuarios`, `usuarios/novo`, `usuarios/<id>/editar`, `.../alternar-ativo`. |
| `templates/` | `usuario_list.html`, `usuario_form.html`. |
| `tests.py` | Cadastro, edição, permissões, auditoria. |

## Relacionamentos

```text
User ─1:1─ Profile ──cliente──▶ Cliente ──segmento──▶ Segmento ──▶ Módulos
User ──groups (1 perfil)──▶ Group ──▶ Permission
Cliente.perfis limita quais Group podem ser atribuídos
```

O **segmento não é do usuário**: ele herda o do cliente (`Profile.segmento` é uma propriedade somente leitura).

## Regras (todas no backend)

- **Escopo**: `usuarios_acessiveis(request.user)` — mesma estrutura matriz/filiais; Admin vê todos. Id de outra estrutura → **404**.
- **Cliente** do formulário: só `clientes_acessiveis`. **Perfil**: só os liberados em `Cliente.perfis` e visíveis (`perfis_visiveis`) — o perfil **Admin** não aparece para quem não é Admin.
- **Admin** só pode ser vinculado ao cliente do sistema (`Cliente.sistema`): `validar_vinculo_admin` na tela, na API e na tela de clientes. Somente um Admin altera outro Admin; superusuário só por superusuário.
- **Permissões especiais** (matriz Herdar/Permitir/Negar por ação, campos `perm_<id>`): validadas em `UsuarioForm.clean` (ids só da matriz de módulos; só se permite o que o solicitante possui) e gravadas em `salvar_usuario` apenas para o usuário editado; auditadas. A regra de precedência está em `apps/acl/README.md`. POST sem campos `perm_*` (ex.: API) não altera exceções.
- Ninguém inativa a própria conta. E-mail único em `auth_user` e `accounts_profile`. Senha com as validações do Django e hash PBKDF2.

## Banco de dados

Sem tabelas próprias. Usa `auth_user` (padrão do Django), `auth_user_groups` e `accounts_profile`:

| Coluna (`accounts_profile`) | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `user_id` | FK | sim | Usuário | 1:1 `auth_user` (CASCADE) |
| `email` | varchar(100) | sim | Igual ao do usuário | **unique** |
| `cliente_id` | FK | não | Cliente do usuário | `cliente_cliente` (PROTECT) |
| `is_verified`, `email_token`, `forget_password_token(_expiration)` | — | — | Fluxo de e-mail/senha | — |
| `created_at` | datetime | sim | — | — |

## Auditoria

Criação, alteração (com `campo / valor anterior / novo valor`, usuário e data) e inativação, via `apps/auditoria`. A senha aparece apenas como "alterada" (hash nunca é gravado).

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| nova regra de quem pode ser vinculado | `forms.UsuarioForm.clean` / `web_project/acesso.py` |
| campo novo no usuário | `forms.py`, `services.foto_usuario`, `api/serializers.py::UsuarioSerializer`, template |
| permissões padrão do Gestor | `auth/management/commands/seed_acl.py` (`EXTRAS`) |
