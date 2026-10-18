# Pagar.me — configuração e operação

> **Estado: implementado pela documentação; NÃO homologado.** Nenhuma chamada ao Pagar.me real foi feita (sem credenciais). Os testes usam um cliente simulado. Itens marcados ⚠ abaixo **não puderam ser confirmados** na documentação e precisam de teste em sandbox antes de produção.

## Documentação consultada (em 18/10/2026)

- Autenticação (Basic, `sk_test_`/`sk_`): https://docs.pagar.me/reference/autenticação-2
- Assinaturas: https://docs.pagar.me/reference/assinaturas-1 · Criar assinatura de plano: https://docs.pagar.me/reference/criar-assinatura-de-plano-1 · Cancelar: https://docs.pagar.me/reference/cancelar-assinatura-1
- Planos / criar plano: https://docs.pagar.me/reference/planos-1 · https://docs.pagar.me/reference/criar-plano-1
- Faturas (status): https://docs.pagar.me/reference/faturas-1 · Cobrança: https://docs.pagar.me/reference/obter-cobrança
- Token de cartão: https://docs.pagar.me/reference/criar-token-cartão-1
- Webhooks e eventos: https://docs.pagar.me/docs/webhooks · https://docs.pagar.me/reference/eventos-de-webhook-1 · https://docs.pagar.me/reference/visão-geral-sobre-webhooks

## O que a documentação confirma (e o código usa)

| Item | Fato |
|---|---|
| URL base | `https://api.pagar.me/core/v5` (sandbox e produção; a **chave** define o ambiente) |
| Auth | HTTP Basic, chave secreta como usuário, senha vazia |
| Plano | `POST /plans` (`name`, `interval`, `interval_count`, `billing_type=prepaid`, `installments=[1]`, `payment_methods`, `items[].pricing_scheme{scheme_type:unit,price}`) |
| Assinatura | `POST /subscriptions` com `plan_id`, `payment_method` (`credit_card`/`boleto`), `customer`, `card_token` |
| Cancelar | `DELETE /subscriptions/{id}` (`cancel_pending_invoices`) |
| Token | `POST /tokens?appId=<chave pública>`, sem `Authorization`, domínio cadastrado no Dash |
| Fatura `status` | `pending, paid, canceled, scheduled, failed` |
| Eventos | `invoice.created/updated/paid/payment_failed/canceled`, `subscription.created/canceled`, `charge.paid/payment_failed/refunded…` |

## ⚠ O que NÃO foi confirmado

1. **Autenticidade do webhook**: as páginas não descrevem assinatura/HMAC/header. Por isso o sistema usa **segredo longo na URL** + **confirmação do pagamento direto na API** (`GET /charges/{id}` deve devolver `paid`) — um webhook forjado não consegue marcar pagamento. Se o Pagar.me oferecer assinatura HMAC no seu painel, acrescente a verificação em `views.WebhookView`.
2. **Formato do payload** do webhook (`id`, `type`, `data.id`, `data.subscription.id`, `data.charge.id`): a documentação não detalha. O código lê esses campos e, se faltarem, **não efetiva o pagamento** (resultado `nao_confirmado_sem_cobranca`; o evento fica registrado). Capture um webhook real em sandbox e ajuste `_sub_id`/`_aplicar_fatura`.
3. **PIX em assinatura**, **teste grátis**, **troca de plano**, **estorno/reembolso**, **listar faturas**: não confirmados/definidos → **fora desta entrega** (a reconciliação usa `GET /subscriptions/{id}`).
4. `card_token` em `POST /subscriptions`: citado na descrição mas ausente do schema da página.
5. Plano: a doc tem inconsistência (`name` vs `description` no item); testar em sandbox.

## Passo a passo

1. No Dash (id.pagar.me → Desenvolvimento → Chaves) copie `sk_test_…` e `pk_test_…`. Cadastre o **domínio** do sistema para permitir a tokenização no navegador.
2. Defina no ambiente (`.env`/servidor), **nunca no código**:
   - `PAGARME_SECRET_KEY` (sk), `PAGARME_PUBLIC_KEY` (pk), `PAGARME_WEBHOOK_TOKEN` (valor longo aleatório: `python -c "import secrets;print(secrets.token_urlsafe(48))"`).
   - Opcionais: `PAGARME_API_URL`, `PAGARME_CONFIRMAR_PAGAMENTO=1` (manter), `ASSINATURA_CARENCIA_DIAS=7`, `ASSINATURA_OBRIGATORIA=0`.
3. No Dash, cadastre o webhook: `https://SEU-DOMINIO/assinatura/webhook/<PAGARME_WEBHOOK_TOKEN>/` com os eventos `invoice.*`, `subscription.*`, `charge.*`.
4. `python manage.py migrate` e agende `python manage.py verificar_assinaturas` a cada hora.
5. Em **Assinatura**, assine o plano com boleto e com cartão de teste; confira no Dash e no sistema.

## Checklist de homologação (sandbox)

- [ ] Plano criado no provedor (`pagarme_plan_id` preenchido) · [ ] assinatura por boleto e por cartão (`token_…`) · [ ] webhook real recebido (anote o formato) · [ ] `invoice.paid` ativa e estende a vigência · [ ] `invoice.payment_failed` → atraso → bloqueio após a carência → pagamento desbloqueia · [ ] cancelamento (sistema e Dash) · [ ] reenvio do mesmo webhook não duplica · [ ] webhook com token errado devolve 404.

## Diagnóstico

Tabela `assinatura_eventowebhook` (`resultado` de cada evento) e a linha do tempo da assinatura. 404 no webhook = token diferente do configurado; 503 = a confirmação na API falhou (o Pagar.me reenvia); `nao_confirmado_*` = payload sem charge ou cobrança ainda não `paid`.

## Rotação de credenciais e rollback

- Trocar a chave: gere outra no Dash, atualize `PAGARME_SECRET_KEY`, reinicie, revogue a antiga. Webhook: gere novo `PAGARME_WEBHOOK_TOKEN`, atualize a URL no Dash e o ambiente (janela curta de 404 é reenviada pelo provedor).
- Desligar a cobrança: remova `PAGARME_SECRET_KEY` (a tela mostra "indisponível"; clientes já bloqueados continuam bloqueados — para liberar todos, defina a assinatura como `ativa` no admin ou apague as linhas). Reverter o código não apaga as tabelas.
