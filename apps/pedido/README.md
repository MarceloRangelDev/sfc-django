# Pedidos e cozinha (`apps/pedido`)

Fila de **produção** ligada ao PDV. A venda (`apps/pdv`) cuida de preço, pagamento, estoque e financeiro; o pedido cuida de **o que a cozinha precisa fazer e em que estado está**. Uma comanda de mesa pode gerar vários pedidos (rodadas).

## Estados

```
NOVO -> CONFIRMADO -> EM_PREPARO -> PRONTO -> ENTREGUE
        (voltar: PRONTO -> EM_PREPARO, EM_PREPARO -> CONFIRMADO)      CANCELADO (de qualquer estado ativo)
```

Cada mudança grava um **evento imutável** (`PedidoEvento`: quem, quando, de/para, nota) e um registro de auditoria.

## Regra de confirmação (por unidade)

Configurável em *Configurações do PDV → Confirmação do pedido* (`pdvconfig_configuracaopdv.confirmacao_pedido`):

| Valor | Quando o pedido vira CONFIRMADO |
|---|---|
| `lancado` | ao ser criado (entra direto na fila) |
| `pagamento` (padrão) | quando a venda é paga (se já está paga, na criação) |
| `manual` | alguém confirma na tela/API |

## Estoque

A baixa dos insumos dos itens acontece **na confirmação** e **uma única vez por item** (`pdv_vendaitem.baixa_estoque_em`). O recebimento da venda só baixa o que ainda não foi baixado. Saldo insuficiente impede a confirmação. Ao entrar **em preparo** o item fica `preparado` (o insumo foi consumido): cancelamento e devolução **não repõem** estoque desses itens. Cancelar um pedido ainda não preparado repõe o estoque e retira os itens da comanda.

## Permissões

- `pedido.view_pedido` ver a fila e os pedidos; `pedido.add_pedido` enviar à cozinha/criar.
- `pedido.mover_pedido` **qualquer perfil que opera** (Operador, Gestor, Administrador) move a fila.
- `pedido.cancelar_pedido` somente Gestor e Administrador (ou permissão especial por usuário).
- Tenant: tudo filtrado pelas unidades do usuário; pedido de outra unidade devolve 404.

## Telas e API

- `/cozinha` (fila com atualização automática), `/pedidos`, `/pedidos/<id>`; envio a partir da comanda do PDV.
- API v1 (`/api/v1/pedidos/`): `POST` (idempotente por `chave`/`Idempotency-Key`), `GET`, `GET fila/`, `POST {id}/confirmar|avancar|voltar|cancelar/`. Pedido avulso abre uma venda ABERTA (origem `pedido`) paga depois por `/pdv/comandas/{venda}/pagar/`.
- Balcão: `registrar_venda(..., cozinha="balcao")` cria o pedido após a venda.

## Tabelas

`pedido_pedido` (unidade, venda, número por unidade, tipo, status, datas de cada etapa, chave de idempotência), `pedido_pedidoitem` (snapshot do que a cozinha vê), `pedido_pedidoevento` (histórico imutável). Colunas novas em tabelas existentes: `pdv_vendaitem.baixa_estoque_em`, `pdvconfig_configuracaopdv.confirmacao_pedido`. Migrações aditivas (`pdv.0003`, `pdvconfig.0002`, `pedido.0001`, `modulo.0015`).

## Limites

Sem impressão de comanda na cozinha (a tela é a fila) e sem integração com iFood/99Food (estrutura prevista na Fase 8).

## Atualização 6 (cozinha em tempo real, Finalizar, entrega em Vendas)

- A fila (`/cozinha`, `GET /api/v1/pedidos/fila/`) passou a incluir pedidos **novos** (antes ocultos até alguém confirmar): coluna **Novos** com a ação “Confirmar”.
- **Finalizar** (pronto → entregue) pede confirmação e mostra o retorno do servidor (sucesso ou erro) num SweetAlert; o JSON de mover traz `mensagem`.
- **Push:** `GET /cozinha/api/eventos` (SSE) avisa quando a fila muda; a tela recarrega sozinha (inclusive após “Enviar à cozinha” na comanda). Ver a seção “Eventos / push” em `apps/pdv/README.md` (unidades da sessão, payload só com a marca, `gthread` no gunicorn).
- O envio da comanda explica o motivo quando não há item novo (comanda vazia × tudo enviado) e a comanda marca cada item como Novo/Enviado.
- Vendas mostra “Entregue” quando todos os pedidos não cancelados da venda foram finalizados (`Venda.entrega`, derivado; sem coluna nova).
- Sem alteração de esquema neste app. Testes: `apps/pedido/tests_push.py`.
