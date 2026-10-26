# apps/modulo — Módulos, Segmentos e habilitação por Cliente

## Objetivo

Controla **o que cada cliente pode usar**. O módulo é a única fonte de verdade de: item de menu (nome, ícone, rota, ordem), permissão exigida e linha na matriz de permissões dos Papéis. Cadastrar um módulo já o coloca no menu e na edição dos papéis — **não existe lista paralela**.

## Estrutura

| Arquivo | Papel |
|---|---|
| `models.py` | `Modulo`, `Segmento`, `ClienteModulo`. |
| `forms.py` | `ModuloForm` (valida rota e permissão contra o que existe) e `SegmentoForm`. |
| `services.py` | `modulos_do_cliente()` e `atualizar_modulos_do_cliente()` (tela e API). |
| `views.py` / `urls.py` | Habilitação por cliente, catálogo de módulos, segmentos. |
| `management/commands/seed_modulos.py` | **Catálogo padrão** (`MODULOS_PADRAO`) + segmentos padrão. |
| `migrations/0003_catalogo_de_modulos.py` | Carrega o catálogo em bancos novos (e nos testes). |
| `templates/` | `modulo_list/form`, `segmento_list/form`, `modulo_cliente`. |

## Relacionamentos

```text
Cliente
   ↓ segmento
Segmento de Trabalho ──modulos (M2M)──▶ Módulo
   ↓
ClienteModulo (cliente × módulo, ativo)
   ↓
Permissão do perfil (Modulo.permissao, ex.: "insumo.view_insumo")
   ↓
Usuário acessa
```

Regra final (`web_project/acesso.py::modulos_habilitados`): o módulo precisa estar **ativo**, e ser **essencial** *ou* (estar no **segmento** do cliente **e** habilitado em `ClienteModulo`). Depois, `module_required` confere a **permissão** do perfil. O Admin enxerga todos os módulos ativos.

## Fluxo

```text
URL ─▶ module_required("slug", "app.view_modelo") ─▶ View ─▶ Service ─▶ Model ─▶ Template
          │
          └─ modulos_habilitados(user)  → 1 consulta por requisição
Menu: context processor menu_modulos ─▶ web_project/menu.py ─▶ Modulo (ordenado) ─▶ menu_item_template.html
```

> Habilitar um módulo também concede as permissões padrão aos perfis padrão (Administrador/Gestor/Operador/Visualizador) do sistema, via `apps/acl/padroes.py` — sem isso o módulo ficaria habilitado mas bloqueado para os usuários do cliente.

## Banco de dados

### `modulo_modulo`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | bigint PK | sim | — | — |
| `nome` | varchar(100) | sim | Nome no menu | — |
| `slug` | slug(50) | sim | Identificador estável usado no código | **unique** |
| `descricao` | varchar(255) | não | — | — |
| `icone` | varchar(100) | não | Classe Tabler, ex.: `ti tabler-app-window` | — |
| `rota` | varchar(100) | não | **Nome da URL** (urls.py). Vazio = sem item de menu | — |
| `app` | varchar(50) | não | App Django do módulo | — |
| `permissao` | varchar(100) | não | `app.view_modelo` (libera menu/tela; base da matriz de papéis) | → `auth_permission` (por nome) |
| `secao` | varchar(20) | sim | `principal` ou `configuracoes` (vira submenu) | — |
| `ordem` | smallint | sim | Posição no menu | — |
| `essencial` | bool | sim | Só depende da permissão (sem segmento/cliente) | — |
| `delegavel` | bool | sim | Aparece na matriz de Papéis | — |
| `instrucoes` | text | não | Instruções de uso (documentação do módulo) | — |
| `ativo` | bool | sim | Interruptor geral (manutenção) | — |
| `criado_em`/`atualizado_em` | datetime | sim | Controle | — |

### `modulo_segmento`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `nome` | varchar(100) | sim | — | **unique** |
| `slug` | slug(50) | sim | — | **unique** |
| `descricao` | varchar(255) | não | — | — |
| `tipo_precificacao` | varchar(10) | sim | `servico`, `produto`, `ambos` (regra de precificação) | — |
| `ativo` | bool | sim | — | — |

`modulo_segmento_modulos` (M2M): `segmento_id` + `modulo_id`, único por par. Clientes apontam para o segmento (`cliente_cliente.segmento_id`, PROTECT).

### `modulo_clientemodulo`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `cliente_id` | FK | sim | — | `cliente_cliente` (CASCADE) |
| `modulo_id` | FK | sim | — | `modulo_modulo` (CASCADE) |
| `ativo` | bool | sim | Habilitado para o cliente | — |
| `atualizado_em` | datetime | sim | — | — |

Único: (`cliente`, `modulo`). Alterações de habilitação são auditadas (`modulo`), assim como cadastro/edição/exclusão de módulos e segmentos.

## Instruções de uso — como adicionar um módulo

```text
Menu principal
    └── Insumos            ← secao = principal
Configurações              ← criado automaticamente quando há submenus visíveis
    └── Clientes           ← secao = configuracoes
```

1. **Crie o app e a tela** (view + `urls.py`) e proteja a URL: `module_required("slug", "app.view_modelo")`.
2. **Cadastre o módulo** em *Configurações > Módulos > Novo Módulo* (ou acrescente em `MODULOS_PADRAO` e rode `python manage.py seed_modulos`):
   - **Ícone**: classe Tabler (`ti tabler-...`); é lido do banco, não do template.
   - **Rota**: o *nome* da URL (`name=` no `path`). O formulário recusa rota inexistente.
   - **Ordem**: menor aparece primeiro. **Seção**: *Menu principal* ou *Configurações*.
   - **Permissão**: use a `view_` do modelo; as demais ações (criar/editar/excluir) são derivadas dela na matriz.
3. **Segmento**: módulo de negócio → marque-o nos segmentos que podem usá-lo (*Segmentos*). Módulo de Configurações → marque **Essencial**.
4. **Cliente**: habilite em *Módulos do cliente* (clientes novos já nascem com os módulos do segmento habilitados).
5. **Papéis**: com *Aparece na edição dos papéis* marcado, o módulo já surge na matriz de *Papéis*. Marque as ações por perfil.
6. **Teste** o menu com um usuário comum e acesse a URL direto para confirmar o bloqueio (403).
7. **Atualize o README** do app.

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| mudar ícone/ordem/nome no menu | a tela *Módulos* (banco) — não mexa em template |
| desligar um módulo para todos | desmarcar *Disponível no sistema* |
| liberar módulo só a um cliente | *Módulos do cliente* |
| criar segmento novo | tela *Segmentos* |
| mudar a regra de liberação | `web_project/acesso.py::modulos_habilitados` |

## Ordem do menu por cliente (atualização 6)

Em **Configurações > Módulos do cliente**, abaixo da habilitação, o administrador reordena os módulos do menu principal do cliente arrastando (SortableJS) ou pelas setas de cada linha (alternativa por teclado). Cada mudança é salva na hora por `POST /modulos/ordem` (`change_clientemodulo`; cliente resolvido em `clientes_acessiveis`); se falhar, a tela volta à última ordem confirmada e mostra o erro. “Restaurar ordem padrão” apaga a personalização.

- Tabela `modulo_ordemmenucliente` (cliente, módulo, ordem; único por par; migração `modulo.0025`). Sem linha vale `Modulo.ordem`.
- `services.modulos_ordenaveis` define o que pode ser reordenado (módulos do segmento, habilitados e ativos, com rota, do menu principal, exceto telas do PDV); `salvar_ordem_do_menu` exige que a lista seja exatamente esse conjunto e reaproveita os valores de ordem já usados (intercala com os não reordenáveis).
- `web_project/menu.py::montar_menu` aplica a ordem do cliente do usuário. Auditado (`modulo`). Testes: `apps/modulo/tests_ordem.py`.
- Limite: Configurações e o grupo PDV mantêm a ordem padrão.
