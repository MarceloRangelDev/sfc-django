# apps/acl — Papéis (perfis) e Permissões

## Objetivo

Telas para criar/editar **Papéis** (`auth.Group`) e marcar, numa matriz *módulo × ação* (Visualizar / Criar / Editar / Excluir), quais permissões nativas do Django cada papel recebe. Não há tabelas próprias.

## Estrutura

| Arquivo | Papel |
|---|---|
| `permissions_matrix.py` | Monta a matriz **a partir da tabela de módulos** (`Modulo.delegavel` e `Modulo.permissao`). |
| `services.py` | `salvar_papel`, `excluir_papel` (transação + auditoria); só aceita ids de permissão da matriz. |
| `views.py` / `urls.py` | Lista, formulário, exclusão e catálogo de permissões. |
| `templates/` | `acl_papel_list/form`, `acl_permissao_list`. |
| `tests.py` | Visibilidade do perfil Admin, Gestor/Operador, proteções. |

## Perfis padrão (criados por `python manage.py seed_acl`)

| Perfil | Visibilidade | Observações |
|---|---|---|
| **Gestor** | sempre visível | Telas de negócio (ver/criar/editar), usuários e notificações da própria estrutura. É o perfil do usuário criado junto com o cliente. |
| **Operador** | sempre visível | Telas de negócio (ver/criar). |
| **Visualizador** | visível | Somente leitura. |
| **Administrador** ("Admin") | **oculto** para quem não é Admin | Acesso global; só no cliente do sistema; não pode ser renomeado nem excluído. |

## Regras

- `perfis_visiveis(user)` (em `web_project/acesso.py`) exclui o Admin das listas, formulários, exclusão e da **API** (`/api/v1/perfis/`) para não-Admins; id do Admin por URL → 404.
- Só o Admin altera as permissões do Admin.
- Novo módulo ⇒ nova linha na matriz **automaticamente**: `Modulo(delegavel=True, permissao="app.view_modelo")`. Módulos como Papéis, Permissões e Auditoria são `delegavel=False`: só o Administrador (via `seed_acl`).
- `ids_validos_da_matriz()` impede, mesmo com POST manipulado, conceder permissões fora da matriz (ex.: `auth.delete_group`).
- **Permissões individuais** (`PermissaoUsuario`, `backends.PermissaoIndividualBackend`): exceção *permitir*/*negar* por usuário e por ação, configurada em Usuários (mesma matriz dos papéis). Precedência: superusuário > exceção **negar** > exceção **permitir** > papel. Vale em `has_perm` (menu, `module_required`, templates e API). Sem exceção, o usuário só herda o papel. Só se *permite* o que o solicitante também possui. Trocar `AUTHENTICATION_BACKENDS` encerra as sessões existentes uma única vez (novo login).
- **Itens da matriz x filiais**: a linha "Itens das filiais" da matriz (`cliente.view/add/change/delete_itensfilial`) libera, para usuários de uma **matriz**, ver/criar/editar/excluir os itens (insumos, categorias…) das suas filiais, **em adição** à permissão do módulo. Itens da própria unidade seguem só a permissão do módulo. Filial nunca alcança itens da matriz nem de outras filiais. O escopo é calculado em `web_project/acesso.py::clientes_dos_itens` e aplicado por `web_project/mixins.py` (`ClienteScopedMixin`/`ListaPorUnidadeMixin`, com `acao_escopo`); telas mostram o selo Matriz/Filial (nome ao passar o mouse) e o filtro "Unidade". Rode `seed_acl` para o Gestor receber ver/criar/editar; a API continua restrita ao cliente do token.
- Cada papel é **global** (não é por cliente); o que é por cliente é quais papéis cada cliente libera (`Cliente.perfis`).

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| nova linha na matriz | cadastre o módulo (ver `apps/modulo/README.md`) |
| permissões padrão de cada perfil | `apps/acl/padroes.py` (usado por `seed_acl`) |
| permissões ao habilitar módulo | automático: `aplicar_padrao_aos_modulos` roda ao salvar "Módulos do cliente", ao criar cliente, no `seed_modulos` e após o `migrate`; só **acrescenta** e só em módulos cujo perfil padrão ainda não tem nenhuma permissão (respeita configuração existente; perfis personalizados nunca são tocados) |
| esconder/mostrar um perfil | `web_project/acesso.py::perfis_visiveis` |


## Papéis: “Outras permissões” recolhido

Na tela de papéis o grupo **Outras permissões** (ações específicas e módulos de apoio) começa **recolhido**, com o interruptor acessível “Mostrar outras permissões” (`aria-controls`/`aria-expanded`, rótulo associado) e um contador das marcadas. O bloco só fica escondido com `d-none`: as caixas continuam no formulário, então **salvar com o grupo fechado preserva exatamente as marcações já salvas** (teste `OutrasPermissoesRecolhidasTests`).

### Como segmento, módulo, papel e permissão especial se combinam (revisão)

`Usuário → Cliente → Segmento → Módulo habilitado → permissão do papel`: (1) o módulo precisa estar no **segmento** do cliente e **habilitado** para ele (módulos *essenciais* — Configurações, Agenda, Fiscal, **Orçamentos** — dependem só da permissão); (2) a permissão vem dos **papéis** do usuário (`Group`), com a regra padrão por perfil (`padroes.py`: Administrador tudo; Gestor ver/criar/alterar; Operador ver/criar; Visualizador ver) mais as permissões **especiais** de `EXTRAS` (ex.: `orcamento.enviar_orcamento` para Administrador e Gestor); (3) o servidor confere os dois (`module_required` + permissão) em toda rota e endpoint; a interface só esconde. Módulos novos entram: no catálogo (`seed_modulos` + migração `modulo.00NN`), em `SCREEN_APPS` (permissões padrão) e numa migração do próprio app que concede as permissões aos perfis **já existentes** (só acrescenta). Para o **Orçamento**: `modulo.0024_modulo_orcamento` e `orcamento.0001_initial`.
