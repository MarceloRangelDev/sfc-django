# Assinatura (planos, cobrança e bloqueio de acesso)

Cobrança recorrente do sistema via **Pagar.me (API v5)**: planos **mensal R$ 59,90** (`5990` centavos) e **anual R$ 429,00** (`42900`), valores guardados no banco (tabela `assinatura_plano`), nunca fixos no código. Configuração do provedor, webhook e homologação: [README_PAGARME.md](README_PAGARME.md).

## Arquitetura

```
 Navegador ──token do cartão (chave pública)──> Pagar.me
     │ POST plano/forma/token                      │  webhooks (POST + segredo na URL)
     v                                             v
 views.py ──> services.py <── WebhookView ──> processar_webhook ──confirma──> GET /charges/{id}
                 │  máquina de estados, bloqueio, verificação
                 ├── pagarme.py  (cliente HTTP mínimo; testes usam um falso)
                 ├── models.py   (Plano, Assinatura, Cobranca, EventoWebhook, EventoAssinatura)
                 └── web_project/acesso.py::modulos_habilitados  ← ÚNICO ponto de bloqueio
```

| Arquivo | Responsabilidade |
|---|---|
| `models.py` | Tabelas (abaixo) |
| `services.py` | **Toda a regra**: estados, webhook idempotente, assinar/cancelar, verificação periódica, leitura da aba |
| `pagarme.py` | Cliente Basic Auth; só endpoints confirmados na documentação |
| `views.py`/`urls.py` | Página `/assinatura/`, ações POST e o webhook público |
| `management/commands/verificar_assinaturas.py` | Rotina agendável (carência, expiração, aviso, reconciliação) |
| `apps/api/assinatura.py` | API v1 (`/api/v1/assinatura/…`) |

## Máquina de estados

```
pendente ──pagou──> ativa ──venceu/falhou──> em_atraso ──carência──> bloqueada
                     │   └─anual venceu─> expirada                      │
                     │                                  pagou ──────────┘ (volta a ativa sozinha)
   qualquer ──cancelou──> cancelada (acesso até `vigente_ate`)
```

- **Pagamento** (`invoice.paid`, confirmado em `GET /charges/{id}` = `paid`): `ativa`, estende `vigente_ate` em um ciclo, limpa atraso/bloqueio. Cada fatura estende **uma vez** (a `Cobranca` vira `paga`).
- **Falha** (`invoice.payment_failed`): `ativa/pendente → em_atraso`. Fatura já paga **nunca** volta a falha (evento fora de ordem).
- **Carência** (`ASSINATURA_CARENCIA_DIAS`, padrão 7): passada, `verificar_assinaturas` bloqueia. Mensal vencida sem pagamento entra em atraso contando do vencimento; **anual vencido expira** (renova pagando de novo).
- **Aviso de renovação**: 30 dias antes de `vigente_ate`, uma vez por ciclo (sino/push para quem tem `assinatura.view_assinatura`).
- **Cancelamento** (pelo usuário ou no provedor): `cancelada`; acesso até `vigente_ate`; depois bloqueia.
- **Upgrade/downgrade e reembolso/estorno: fora desta entrega** (a regra de proporcionalidade e os endpoints não foram definidos/confirmados). Hoje: cancelar e assinar o outro plano. Teste grátis e PIX em assinatura também ficaram de fora (ver README_PAGARME.md).

## Bloqueio de acesso (um ponto só)

`web_project/acesso.py::modulos_habilitados` consulta `services.acesso_bloqueado(cliente)`; se bloqueado, o usuário só enxerga os módulos `assinatura`, `painel` e `perfil-foto`. Telas (`module_required`), menu e API (`PermissaoPorAcao`/`PermissaoDoSistema`) usam essa mesma função, então **nada precisa ser repetido** nas views. Telas respondem 403 e a API **403** com a mensagem "Acesso bloqueado por falta de pagamento da assinatura…". Administrador global/superusuário nunca é bloqueado. O tenant é a **matriz** (filiais são cobertas). **Cliente sem assinatura continua liberado** (para não travar quem já usa o sistema); `ASSINATURA_OBRIGATORIA=1` muda isso.

## Segurança

- Chaves só por ambiente (`PAGARME_SECRET_KEY`, `PAGARME_WEBHOOK_TOKEN`); nada em banco/log/resposta. Cartão: o navegador tokeniza direto no Pagar.me; os campos do cartão **não têm `name`** e o servidor só aceita `token_…`.
- Webhook: segredo na URL (comparação em tempo constante, 404 se errado), limite de 64 KB e 600 req/min por IP, **confirmação do pagamento na API** antes de efetivar, `hook_id` único (idempotência), nenhum payload completo gravado.
- Aba/página: `assinatura.view_assinatura`; ações: `assinatura.gerenciar_assinatura`. Tenant derivado do usuário (`clientes_acessiveis`); fora dele → 404. Gestor e Administrador recebem as permissões; Operador/Visualizador não.
- Auditoria: criação e mudanças de status (módulo `assinatura`), sem dados de cartão.

## Banco de dados

| Tabela.coluna | Tipo | Nulo | Padrão | Observação |
|---|---|---|---|---|
| `assinatura_plano.slug` | slug(30) único | não | — | `mensal`, `anual` |
| `.nome` / `.valor` | varchar(64) / bigint | não | — | valor em **centavos** |
| `.intervalo` / `.intervalo_qtd` | `month`\|`year` / smallint | não | `month` / 1 | |
| `.pagarme_plan_id` | varchar(60) | não | "" | preenchido ao sincronizar |
| `.ativo` / `.atualizado_em` | bool / datetime | não | true / auto | |
| `assinatura_assinatura.cliente_id` | **OneToOne** Cliente (PROTECT) | não | — | cliente **raiz** (matriz); tenant |
| `.plano_id` | FK Plano (PROTECT) | não | — | |
| `.status` | varchar(10), índice | não | `pendente` | estados acima |
| `.forma_pagamento` | `boleto`\|`credit_card` | não | `boleto` | |
| `.pagarme_subscription_id` | varchar(60), índice | não | "" | `sub_…` |
| `.vigente_ate`, `.proxima_cobranca_em` | datetime | sim | null | |
| `.em_atraso_desde`, `.bloqueada_em`, `.cancelada_em`, `.aviso_vencimento_em` | datetime | sim | null | |
| `.iniciada_em`/`.atualizado_em` | datetime | não | auto | |
| `assinatura_cobranca.assinatura_id` | FK (PROTECT) | não | — | |
| `.pagarme_invoice_id` | varchar(60) **único** | não | — | `in_…` |
| `.pagarme_charge_id` | varchar(60) | não | "" | |
| `.valor` | bigint | não | 0 | centavos |
| `.forma_pagamento`, `.status` | varchar | não | "" / `pendente` | `pendente/paga/falhou/cancelada` |
| `.vencimento`, `.pago_em` | datetime | sim | null | |
| `.url_pagamento` | url(500) | não | "" | 2ª via (só `https://`) |
| `assinatura_eventowebhook.hook_id` | varchar(80) **único** | não | — | idempotência |
| `.tipo`, `.resultado` | varchar | não | — / "" | |
| `.recebido_em`/`.processado_em` | datetime | não / sim | auto / null | |
| `assinatura_eventoassinatura` | FK, tipo, descricao(200), criado_em | não | — | linha do tempo |

Migrações: `0001` (tabelas novas) e `0002` (planos iniciais com `get_or_create` — nunca sobrescreve valor alterado — e permissões a Administrador/Gestor); `modulo/0023` (módulo essencial "Assinatura" em Configurações). Nenhuma tabela existente é alterada; reversíveis (o rollback das permissões/módulo é no-op).

## Como operar e testar

Agendar a cada hora: `python manage.py verificar_assinaturas` (idempotente). Testes: `manage.py test apps.assinatura` (Pagar.me sempre simulado; cobre criação, renovação, falha→carência→bloqueio→regularização, anual, cancelamento, webhook duplicado/inválido/fora de ordem, tenant, permissões, API e Swagger).
