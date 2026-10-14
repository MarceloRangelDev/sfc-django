# Lista de compras

Lista de compras de cada unidade (matriz/filial), com **sugestões automáticas do estoque**, itens manuais e **serviços avulsos**. Os itens alimentarão as cotações (Fase 4).

## Como a quantidade sugerida é calculada

Para cada insumo **ativo** da unidade com mínimo definido (> 0) e saldo abaixo dele, que ainda **não está na lista**:

```
bruto    = mínimo × fator_de_reposição − saldo
sugerido = bruto, arredondado PARA CIMA ao múltiplo de compra (se houver)
```

Tudo na unidade do insumo. O **fator de reposição** (padrão 2 = "repor até o dobro do mínimo") e o **múltiplo de compra** (tamanho da embalagem, opcional) são configurados por insumo em *Estoque > insumo > mínimo* ou em `PUT /estoque/saldos/{insumo}/minimo/`.

| Mínimo | Fator | Saldo | Múltiplo | Bruto | Sugerido |
|---|---|---|---|---|---|
| 5 kg | 2 | 3 kg | — | 7 | **7 kg** |
| 5 kg | 2 | 3 kg | 5 kg | 7 | **10 kg** |
| 5 kg | 2 | 0 | 5 kg | 10 | **10 kg** (já é múltiplo) |

A tela mostra a conta de cada sugestão e a grava na observação do item. O **servidor sempre recalcula**: quem inclui só diz *quais* insumos (ou todos), nunca a quantidade. A inclusão é **idempotente** (reenvio/duplo clique não duplica; há restrição única `cliente + insumo` no banco).

## Itens

- **De insumo:** nome copiado do insumo; no máximo um por insumo; a unidade deve ter a mesma dimensão da do insumo.
- **Avulso / serviço:** texto livre (até 150), qualquer unidade (ex.: `un`).
- Adicionar, editar e remover (exclusão física) são **auditados** (módulo `compras`) na mesma transação. Observação até 255 caracteres.
- **Tenant:** a lista é da unidade do usuário; a matriz vê as filiais e só altera com `cliente.<ação>_itensfilial`. Fora do escopo = 404. Sugestões e inclusões valem só para a **própria** unidade. Insumo de outra empresa é recusado pelo formulário.

## Telas e API

Rotas (`module_required("compras", ...)`): `compras` (lista + painel de sugestões), `compras/novo`, `compras/<id>/editar`, `compras/<id>/excluir` (POST), `compras/sugestoes/adicionar` (POST, `insumo` repetível; vazio = todas).

| Método | Rota (`/api/v1`) | Descrição | Permissão |
|---|---|---|---|
| GET/POST | `/compras/itens/`, `/compras/itens/{id}/` | Lista (`q`, `origem`, `ordenar`) / cria / consulta / PUT / PATCH / DELETE | `*_itemcompra` |
| GET | `/compras/sugestoes/` | Sugestões com a conta | `view_itemcompra` |
| POST | `/compras/sugestoes/adicionar/` | `{"insumos": [ids]}` (omitido = todas); idempotente; 201 se criou, 200 se nada novo | `add_itemcompra` |

## Banco de dados

Migration `compras.0001_lista_de_compras` (tabela nova) e `modulo.0012_modulo_compras` (catálogo, ordem 17, nos segmentos padrão e habilitado para clientes com segmento).

**`compras_itemcompra`**

| Coluna | Tipo | Nulo | Padrão | Observação |
|---|---|---|---|---|
| `id` | bigint PK | não | auto | |
| `cliente_id` | FK `cliente_cliente` (PROTECT) | não | — | Unidade dona (tenant) |
| `insumo_id` | FK `insumo_insumo` (PROTECT) | sim | NULL | Vazio = item avulso/serviço. Único por (`cliente_id`, `insumo_id`) quando preenchido |
| `descricao` | varchar(150) | não | — | Nome do insumo (copiado) ou texto livre |
| `quantidade` | decimal(15,4) | não | — | > 0 |
| `medida_id` | FK `medidas` (PROTECT) | não | — | |
| `observacao` | varchar(255) | não | `''` | |
| `origem` | varchar(10) | não | `manual` | `sugestao` / `manual` |
| `criado_por_id` | FK usuário (SET NULL) | sim | NULL | |
| `criado_em`, `atualizado_em` | datetime | não | auto | |

Índice (`cliente_id`, `origem`). Impacto em dados existentes: nenhum.

## Permissões

`compras.view/add/change/delete_itemcompra`, entregues pelos padrões dos perfis (`SCREEN_APPS`): Administrador (tudo), Gestor (ver/criar/editar), Operador (ver/criar), Visualizador (ver).
