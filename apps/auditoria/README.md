# apps/auditoria — Auditoria centralizada

## Objetivo

Registro único de **quem fez o quê, em qual registro, quando e o que mudou**. Todos os módulos usam o mesmo serviço — **não crie outro sistema de auditoria**.

## Estrutura

| Arquivo | Papel |
|---|---|
| `models.py` | `RegistroAuditoria`. |
| `utils.py` | `registrar`, `registrar_criacao`, `registrar_edicao`, `registrar_alteracoes`, `registrar_exclusao`, `tirar_foto`/`comparar`. |
| `mixins.py` | `AuditoriaFormMixin`: audita automaticamente Create/UpdateView (foto antes → compara → grava na mesma transação). |
| `signals.py` | Login/logout. |
| `filtros.py` / `views.py` / `urls.py` | Tela administrativa com filtros e paginação. |
| `tests.py` | Registro, comparação, escopo. |

## O que é auditado

Criação, alteração (campo, valor anterior, novo valor), exclusão e eventos como: alteração de permissões de papéis, de perfil/cliente de usuários, de vínculo cliente/matriz/filial/segmento, habilitação e desabilitação de módulos, cadastro de módulos e segmentos, **envio de notificações**.

Na alteração, cada item de `alteracoes` guarda `campo`, `rotulo`, `antes`, `depois`; o registro guarda ID, nome, usuário, cliente, IP e data/hora.

## Uso

```python
with transaction.atomic():                      # auditoria e dado na MESMA transação
    ...
    registrar_criacao(request, objeto, modulo="meu-modulo")
    registrar_edicao(request, objeto, foto_antes)               # foto = tirar_foto(objeto) antes de salvar
    registrar_alteracoes(request, objeto, [{"campo": "x", "rotulo": "X", "antes": "a", "depois": "b"}])
    registrar_exclusao(request, objeto)                         # ANTES do delete()
```

CRUD usa `silencioso=False`: se não der para auditar, a operação inteira é desfeita.

## Escopo (isolamento)

`views.registros_visiveis(user)`: Admin vê tudo; os demais, só registros de clientes da própria estrutura (`clientes_acessiveis`). Vale também para `/api/v1/auditoria/`.

## Banco de dados — `auditoria_registroauditoria`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `usuario_id` | FK | não | Quem executou | `auth_user` (SET NULL) |
| `cliente_id` | FK | não | Cliente do usuário na época | `cliente_cliente` (SET NULL) |
| `acao` | varchar(50) | sim | criar, editar, excluir, login... | — |
| `modulo` | varchar(50) | não | Módulo de origem | — |
| `tabela` | varchar(100) | não | Tabela afetada | — |
| `registro_id` / `registro_nome` | varchar | não | Registro afetado | — |
| `descricao` | text | não | Texto legível | — |
| `alteracoes` | JSON | sim | Lista `{campo, rotulo, antes, depois}` | — |
| `ip` | IP | não | — | — |
| `resultado` | varchar | sim | sucesso/falha | — |
| `criado_em` | datetime | sim | Data/hora | — |

Índices: (`modulo`, `criado_em`), (`cliente`, `criado_em`).

## Manutenção

Novo módulo auditado → use `AuditoriaFormMixin` nas views e `registrar_*` nos services. Valores especiais (dinheiro) → `AUDITORIA_MONETARIOS` no model.
