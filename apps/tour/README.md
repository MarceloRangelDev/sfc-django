# apps/tour — Tours de página reutilizáveis

## Objetivo

Guiar o usuário pelos elementos de cada tela. Há **um único algoritmo** (`src/assets/js/tour.js`, baseado no Shepherd); cada página só declara a lista de passos. O botão **Não mostrar novamente** grava a preferência **por usuário**.

## Estrutura

| Arquivo | Papel |
|---|---|
| `src/assets/js/tour.js` | `initTour({page, steps})`; carrega o Shepherd sob demanda. Incluído em `templates/layout/partials/scripts.html`. |
| `models.py` | `TourPreferencia(usuario, pagina)` — existe linha = "não mostrar mais". |
| `views.py` / `urls.py` | `GET /tour/<pagina>/` (estado) e `POST /tour/<pagina>/ocultar`. |
| `tests.py` | Preferência por usuário/página, idempotência, login. |

## Como criar um tour para uma página

1. Marque os elementos com `data-tour="..."` (ou use qualquer seletor CSS).
2. No `{% block page_js %}` do template (precisa ser neste bloco: o `tour.js` carrega depois do conteúdo):

```html
{% block page_js %}
<script>
  initTour({
    page: 'insumos',   // chave da página; basta ser única (slug, até 50 caracteres)
    steps: [
      { element: '[data-tour="novo"]', title: 'Novo insumo', text: 'Cadastre um insumo.', position: 'bottom' },
      { element: '[data-tour="tabela"]', title: 'Tabela', text: 'Seus insumos.', position: 'top' }
    ]
  });
</script>
{% endblock page_js %}
```

Não é preciso migration nem cadastro: a chave `page` é livre. Passos cujo elemento não existe (ex.: botão que o usuário não tem permissão de ver) são pulados.

## Comportamento

- Abre sozinho na primeira visita; a cada passo há **Voltar / Próximo / Concluir** e **Não mostrar novamente**.
- Depois de "Não mostrar novamente" não abre mais sozinho, mas o botão **?** (canto inferior direito) inicia o tour quando o usuário quiser.
- `auto: false` em `initTour` desliga a abertura automática (só o botão **?**).

Tours existentes: **Insumos** (`apps/insumo/templates/insumo.html`), **Clientes** e **Categorias**, mais os do registro `tours-modulos.js` (módulos de negócio) e, nesta atualização, as telas que ainda não tinham: Lista de compras, Consumidores, Cotações, Medidas, Mão de obra (colaboradores e apontamentos), Usuários, Papéis, Mesas, Formas de pagamento, Insumos globais, Histórico de notificações, Pedidos, Vendas do PDV, Auditoria, Notas fiscais, Integrações e Templates do WhatsApp (lista e formulário). Telas de formulário simples, detalhe, impressão/comprovante, a tela do PDV/cozinha (operação em tempo real) e a Agenda/Kanban (que têm interações próprias) ficaram sem tour.

## Banco de dados — `tour_tourpreferencia`

| Coluna | Tipo | Obrig. | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | PK | sim | — | — |
| `usuario_id` | FK | sim | Quem pediu para ocultar | `auth_user` (CASCADE) |
| `pagina` | slug(50) | sim | Chave usada em `initTour` | — |
| `criado_em` | datetime | sim | — | — |

Único: (`usuario`, `pagina`).

## Segurança

Os endpoints exigem login e só leem/gravam a preferência do **próprio** usuário (não há id de usuário na URL). A gravação é `POST` com CSRF (o JS envia o `X-CSRFToken` do cookie).

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| visual/botões do tour | `tour.js` (`criarTour`) |
| tour de uma tela nova | só o template da tela (acima) |
| tour dos módulos (processados, canais, categorias, produtos, fichas, precificação, custos, estoque, financeiro, painel) | registro `src/assets/js/tours-modulos.js` (`TOURS`) + `<script>initTourModulo('chave');</script>` no `page_js` da tela; `apps/tour/tests_modulos.py` confere chaves, seletores e telas |
| "reativar" o tour de alguém | apagar a linha em `TourPreferencia` |
