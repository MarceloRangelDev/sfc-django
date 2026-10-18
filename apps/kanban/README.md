# Kanban (somente administrador)

Quadro simples por Cliente: colunas configuráveis e cartões (título, descrição, responsável, prazo). Persistido em banco; arrastar e soltar usa o `jkanban` que já vem no template (`src/assets/vendor/libs/jkanban`), sem dependência nova.

## Arquitetura

```
tela /kanban (kanban.html + jkanban) ──POST──> views.py (AcaoView, JSON) ──> services.py <── api/kanban.py (/api/v1/kanban/…)
                                                                           └─> auditoria (modulo="kanban")
```

| Arquivo | Responsabilidade |
|---|---|
| `models.py` | `Coluna`, `Cartao` |
| `services.py` | **Toda a regra**: acesso, tenant, validação, ordem, auditoria |
| `views.py` / `urls.py` | Tela e ações POST (CSRF) com `module_required("kanban", permissão)` |
| `apps/api/kanban.py` | Mesma regra na API v1 (tag `kanban`) |

## Acesso e multi-tenant

- Só `administrador_global` (superusuário ou grupo Administrador). Validado em **três** pontos: permissões da rota/API (só o grupo Administrador as recebe), `services.contexto()` (mesmo que alguém receba a permissão manualmente → 403) e na API.
- O quadro é o do Cliente do usuário autenticado; nada de `cliente_id` vindo do navegador. Coluna/cartão de outro Cliente → 404 (igual a "não existe"). Responsável precisa ser do mesmo Cliente.
- Excluir coluna só se vazia. Máximo de 12 colunas.
- Mover (`kanban.mover_cartao`): na mesma transação, com `select_for_update`, reinserimos o cartão na posição pedida e renumeramos origem e destino. Mudança de coluna é auditada.

## Endpoints

Web (POST): `kanban/colunas/nova`, `…/<id>/renomear`, `…/<id>/excluir`, `kanban/cartoes/novo`, `…/<id>/editar`, `…/<id>/excluir`, `…/<id>/mover`.
API v1: `GET kanban/quadro/`, `POST kanban/colunas/`, `PATCH|DELETE kanban/colunas/<id>/`, `POST kanban/cartoes/`, `PATCH|DELETE kanban/cartoes/<id>/`, `POST kanban/cartoes/<id>/mover/` (`{coluna, posicao}`). Erros: 401/403/404/422 no envelope padrão.

## Banco

| Tabela.coluna | Tipo | Nulo | Padrão | Observação |
|---|---|---|---|---|
| `kanban_coluna.cliente_id` | FK Cliente (PROTECT) | não | — | tenant |
| `.nome` | varchar(80) | não | — | |
| `.ordem` | int ≥ 0 | não | 0 | renumerada pelo serviço |
| `.criado_em` | datetime | não | auto | |
| `kanban_cartao.cliente_id` | FK Cliente (PROTECT) | não | — | tenant (redundante com a coluna, de propósito) |
| `.coluna_id` | FK Coluna (PROTECT) | não | — | |
| `.titulo` | varchar(150) | não | — | |
| `.descricao` | text (≤2000 validado) | não | "" | |
| `.responsavel_id` | FK User (SET_NULL) | sim | null | mesmo Cliente |
| `.prazo` | date | sim | null | |
| `.ordem` | int ≥ 0 | não | 0 | |
| `.criado_por_id` | FK User (SET_NULL) | sim | null | |
| `.criado_em`/`.atualizado_em` | datetime | não | auto | |

Migrações: `0001` (tabelas novas, sem efeito em dados existentes; reversível), `0002` (cria permissões e concede ao grupo Administrador; reversível sem efeito), `modulo/0022` (módulo essencial "Kanban" em Configurações). O primeiro acesso cria as colunas "A fazer / Em andamento / Concluído".

## Testar

`manage.py test apps.kanban` (acesso, tenant, ordem ao mover, auditoria, API).
