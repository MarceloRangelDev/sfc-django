# web_project — regras centrais de acesso, menu e layout

## Objetivo

Reúne o que **todos** os apps compartilham: tema Vuexy (`TemplateLayout`), decorators de proteção de URL, **regras de isolamento entre clientes** e o **menu dinâmico**. Antes de criar uma regra de acesso em outro lugar, procure aqui — a regra deve existir **uma única vez**.

## Estrutura

| Arquivo | Para que serve |
|---|---|
| `acesso.py` | **Fonte única** das regras de escopo: quem é Admin, quais clientes/usuários/perfis/módulos cada usuário enxerga. |
| `decorators.py` | `screen_required` (login + permissão) e `module_required` (login + permissão + módulo liberado). |
| `menu.py` | Monta o menu lateral a partir da tabela `modulos_modulo`. |
| `mixins.py` | `ClienteScopedMixin`: filtra CRUDs de negócio (Insumo, Categoria...) pelo cliente do usuário. |
| `template_tags/theme.py` | Filtros de template (`has_permission`, `has_module`...). |
| `regras/` | Regras de negócio puras (dinheiro, conversão de medidas, validadores). |
| `testing.py` | `habilitar_modulo()` para testes. |
| `__init__.py` | `TemplateLayout.init(self, context)`: monta o contexto do tema. |

## A cadeia de acesso

```text
Usuário
   ↓  auth.models.Profile.cliente
Cliente
   ↓  Cliente.segmento
Segmento de Trabalho
   ↓  Segmento.modulos  (+ ClienteModulo.ativo)
Módulo habilitado
   ↓  Modulo.permissao  (permissão nativa do Django do perfil/Group)
Acesso
```

Implementada em `acesso.modulos_habilitados(user)` (segmento + habilitação do cliente) e, em seguida, `module_required` / `PermissaoDoSistema` (permissão do perfil). Módulos **essenciais** (Configurações) ignoram segmento e habilitação: só dependem da permissão.

## Funções de `acesso.py`

| Função | Resultado |
|---|---|
| `administrador_global(user)` | `True` para superusuário ou grupo `Administrador` (o perfil "Admin"). |
| `clientes_acessiveis(user)` | Próprio cliente + matriz + filiais da matriz. Admin: todos. Sem cliente: nenhum. |
| `usuarios_acessiveis(user)` | Usuários cujo cliente está em `clientes_acessiveis`. Superusuário só aparece para superusuário. |
| `perfis_visiveis(user)` | Todos os grupos, **exceto `Administrador`** para quem não é Admin. |
| `validar_vinculo_admin(perfil, cliente)` | Mensagem de erro se o perfil Admin for vinculado a um cliente que não é o do sistema. |
| `modulos_habilitados(user)` | Slugs liberados ao usuário (1 consulta por requisição; fica em `user._modulos_habilitados`). |

### Matriz / Filial (código complexo)

```text
Matriz A (raiz)        Cliente.matriz = NULL, tipo = matriz
 ├── Filial A1         Cliente.matriz = A, tipo = filial
 └── Filial A2         Cliente.matriz = A, tipo = filial
```

`Cliente.raiz_id` devolve `matriz_id or pk`. O filtro é `Q(pk=raiz) | Q(matriz_id=raiz)`: **quem está na matriz ou em qualquer filial enxerga a estrutura inteira** (clientes, usuários, auditoria, notificações) e nada de outra estrutura. Os **dados de negócio** (Insumos, Categorias...) continuam isolados por cliente exato (`ClienteScopedMixin`): uma filial não vê os insumos da matriz.

## Como proteger uma tela nova

```python
# urls.py
path("minha-tela", module_required("meu-modulo", "meuapp.view_meumodelo")(MinhaView.as_view()), name="minha-tela")
```

```python
# views.py — nunca confie num id vindo da URL/POST
objeto = get_object_or_404(MeuModel.objects.filter(cliente=cliente_do_usuario(request.user)), pk=pk)
# ou, para clientes/usuários: get_object_or_404(clientes_acessiveis(request.user), pk=pk)
```

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| mudar quem enxerga quais clientes/usuários | `acesso.py` (`clientes_acessiveis`, `usuarios_acessiveis`) — e só ali |
| mudar a regra de módulo habilitado | `acesso.modulos_habilitados` |
| trocar a aparência/estrutura do menu | `menu.py` e `templates/layout/partials/menu/vertical/` |
| adicionar item de menu | cadastre um **Módulo** (ver `apps/modulo/README.md`) |
| mudar o nome do perfil Admin | `acesso.GRUPO_ADMINISTRADOR` (e rode `seed_acl`) |
