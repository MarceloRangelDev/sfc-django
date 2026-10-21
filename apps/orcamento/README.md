# apps/orcamento — Orçamentos

## Objetivo

Montar propostas de preço de **produtos, serviços ou os dois**, com descontos e totais calculados pelo servidor, e entregá-las ao cliente final por **PDF (impressão/download)**, **e-mail** (PDF em anexo) e **WhatsApp** (resumo + link do PDF). Módulo `orcamento` (menu principal, ordem 16), **essencial**: depende só da permissão do perfil, em qualquer segmento.

## Fluxos

1. **Criar/editar** (`/orcamentos/novo`, `/orcamentos/<id>/editar`): destinatário (cliente final do cadastro, cujos contatos são *copiados*, ou digitado), validade, itens, desconto geral e observações. Cada item é um produto/serviço da unidade ou uma linha **avulsa** (descrição + tipo). Sem preço digitado, usa o preço da precificação marcada para o PDV do produto. Os totais mostrados enquanto se digita vêm de `POST /orcamentos/calcular` (nada é calculado no navegador).
2. **Situação**: `rascunho → enviado` (automático ao enviar por e-mail/WhatsApp com sucesso) e, a partir de rascunho/enviado, `aprovado`, `recusado` ou `cancelado` (finais; reabrir = criar outro). Só rascunho/enviado é editável. Excluir só vale para **rascunho nunca enviado**; os demais se cancelam (histórico preservado).
3. **Enviar / imprimir** (`/orcamentos/<id>/enviar`): o usuário **desmarca itens que não devem ir naquela versão**. Isso afeta só o PDF/mensagem gerados; o orçamento salvo **não muda**. Remover os itens também do orçamento salvo exige marcar “alteração permanente” **e** a confirmação explícita (`confirmar_persistir`), a permissão de alterar e orçamento editável; é auditado. Não se pode omitir todos os itens, nem deixar o desconto geral maior que o total da versão.
4. **PDF** (`/orcamentos/<id>/pdf`, inline ou `?baixar=1`): versão completa, para imprimir.

## Regras de negócio importantes

- Dinheiro em **centavos**; quantidade com 3 casas; linha = `round_half_up(quantidade × preço) − desconto` (desconto da linha ≤ valor da linha); `total = subtotal − descontos dos itens − desconto geral` (nunca negativo). Tudo em `services.calcular_totais`; total/subtotal enviados pelo cliente são ignorados.
- Numeração **sequencial por unidade** (`numero`), com retry se duas gravações disputarem o mesmo número (restrição única `cliente + numero`).
- Produto/serviço do item: **da mesma unidade** e ativo (o já usado no orçamento continua válido se for desativado depois). O tipo (`produto`/`servico`) e o nome são copiados para o item.
- Quem vê o quê: `clientes_dos_itens(user, ação)` (própria unidade; matriz com `cliente.<ação>_itensfilial` também as filiais). Outra unidade = **404**; produto de outra unidade = erro de validação (422 na API).
- Editar, cancelar ou excluir **apaga os links públicos** do orçamento.

## Permissões

Permissões do modelo + uma especial: `orcamento.enviar_orcamento` (“Enviar orçamento por e-mail ou WhatsApp”, aparece em *Outras permissões* dos papéis).

| Perfil | Pode |
|---|---|
| Administrador | tudo (inclui excluir rascunho e enviar) |
| Gestor | ver, criar, alterar, aprovar/recusar/cancelar e **enviar** |
| Operador | ver e criar (não altera, não envia) |
| Visualizador | ver e gerar PDF |

Gerar/imprimir PDF só exige *ver*. Enviar por **e-mail**: `enviar_orcamento` + e-mail configurado. Enviar por **WhatsApp**: `enviar_orcamento` + `integracao.enviar_mensagem` + conexão de WhatsApp **ativa da unidade do orçamento** (`whatsapp_cloud`, `whatsapp_evolution` ou `whatsapp_intermediario`).

## Canais de envio e configuração

- **E-mail**: usa o backend do projeto. Com `ResendEmailBackend` exige `RESEND_API_KEY` e `MAIL_FROM_EMAIL`; sem isso o canal aparece como indisponível. O backend foi ampliado para repassar **anexos** (Base64) ao Resend (`config/email_backend.py`). O `Reply-To` é o e-mail de contato da empresa, se houver.
- **WhatsApp**: os conectores só enviam texto, então a mensagem leva o resumo e o **link público do PDF** (`/orcamento/publico/<token>`, base `BASE_URL`). A mensagem entra na **fila de saída** da integração: “ok” significa “na fila”; o resultado final fica em *Integrações → Mensagens*.
- **Link público**: token de 32 bytes (`secrets.token_urlsafe`), vale **7 dias**, só desse orçamento e dos mesmos itens omitidos; limite de 60 downloads/min por IP; `Cache-Control: private, no-store` e `X-Robots-Tag: noindex`. Cancelado/recusado/alterado/excluído = 404.
- Falhas (PDF, e-mail, integração) viram mensagem clara e um registro em `OrcamentoEnvio`; o orçamento **nunca** é perdido nem muda de situação por causa delas. Detalhes técnicos vão só ao log do servidor (sem destino nem conteúdo).

## PDF (reportlab, `pdf.py`)

Cabeçalho: logo da **empresa** (só se houver; módulo de imagens, entidade `cliente`), nome/fantasia, CPF/CNPJ, endereço, e-mail e WhatsApp da empresa (o e-mail de quem criou entra só se a empresa não tem e-mail e ele é da mesma unidade), título “ORÇAMENTO Nº X”, situação e validade, e **data/hora de emissão no canto inferior direito do cabeçalho**. Corpo: destinatário, tabela de itens (cabeçalho repetido a cada página), totais (juntos, sem quebrar) e observações. Rodapé: **Faturando Certo** e “Página X de Y”; páginas seguintes têm uma faixa curta com número e data. Todo texto é escapado; só entram dados da unidade dona.

## Tabelas (migração `orcamento.0001_initial`, aditiva)

**`orcamento_orcamento`** — o orçamento.

| Coluna | Tipo | Nulo | Padrão | Finalidade |
|---|---|---|---|---|
| `id` | bigint PK | não | auto | — |
| `cliente_id` | FK → `cliente_cliente` (PROTECT) | não | — | Unidade (tenant) dona |
| `numero` | int ≥ 0 | não | gerado | Número sequencial por unidade |
| `consumidor_id` | FK → `consumidor_consumidor` (SET NULL) | sim | null | Cliente final do cadastro (os dados são copiados abaixo) |
| `destinatario_nome` | varchar(150) | não | — | Destinatário |
| `destinatario_email` | varchar(254) | não | `''` | E-mail do destinatário (dado pessoal: auditoria só diz que mudou) |
| `destinatario_whatsapp` | varchar(20) | não | `''` | WhatsApp do destinatário (idem) |
| `validade` | date | sim | null | Válido até |
| `observacoes` | text(1000) | não | `''` | Observações |
| `desconto` | bigint ≥ 0 | não | 0 (`db_default`) | Desconto geral (centavos) |
| `status` | varchar(10) | não | `rascunho` (`db_default`) | rascunho/enviado/aprovado/recusado/cancelado |
| `subtotal` / `desconto_itens` / `total` | bigint ≥ 0 | não | 0 | Calculados (centavos) |
| `enviado_em` | datetime | sim | null | Primeiro envio com sucesso |
| `criado_por_id` | FK → usuário (SET NULL) | sim | null | Autor |
| `criado_em` / `atualizado_em` | datetime | não | auto | — |

Restrição única `orcamento_numero_unico_por_unidade (cliente, numero)`; índice `orcamento_cliente_status_idx (cliente, status)`; permissão `enviar_orcamento`.

**`orcamento_orcamentoitem`** — `orcamento_id` (FK CASCADE), `produto_id` (FK PROTECT, nulo = linha avulsa), `tipo` (`produto`/`servico`, padrão `produto`), `descricao` (150), `quantidade` (decimal 10,3 > 0), `preco_unitario` (centavos), `desconto` (centavos, 0), `total` (calculado), `ordem`.

**`orcamento_orcamentoenvio`** — registro de PDF/e-mail/WhatsApp: `orcamento_id` (CASCADE), `canal` (`pdf`/`email`/`whatsapp`), `status` (`ok`/`falha`), `destino` (**mascarado**, ex.: `m***@dominio`), `detalhe` (mensagem segura), `itens_omitidos` (JSON de ids), `criado_por_id`, `criado_em`.

**`orcamento_orcamentolink`** — link público: `orcamento_id` (CASCADE), `token` (único), `itens_omitidos` (JSON), `expira_em` (índice), `criado_em`.

A migração também cria as permissões do módulo e as concede aos perfis padrão **já existentes** (Administrador, Gestor, Operador, Visualizador), só acrescentando. O catálogo ganha o módulo em `modulo.0024_modulo_orcamento`.

## API (`/api/v1/orcamentos/`, Swagger em `/api/docs/`, tag *orcamentos*)

`GET/POST /orcamentos/` · `GET/PATCH/PUT/DELETE /orcamentos/{id}/` · `POST /orcamentos/{id}/aprovar|recusar|cancelar/` · `GET /orcamentos/{id}/pdf/` (`application/pdf`, `?omitir=1,2`) · `GET /orcamentos/{id}/canais/` · `POST /orcamentos/{id}/enviar/` (`canal`, `omitir_itens`, `persistir_alteracao` + `confirmar_persistencia`, `destinatario`, `conexao`). Listagem paginada (`data.itens` + `data.paginacao`) com `q` e `situacao`. PATCH sem `itens` mantém os itens; com `itens` substitui todos. Situação fechada → 422; outra unidade → 404; sem permissão → 403.

## Pontos delicados para manutenção

- `services.versao_para_envio` (versão em memória) é a **única** fonte do que vai para PDF/e-mail/WhatsApp; nunca gere PDF a partir do orçamento salvo sem passar por ela.
- `services.aplicar_omissao_persistente` é a única forma de apagar itens por causa de envio; exige confirmação em quem chama (tela e API).
- Link público: não devolva dados além do PDF; qualquer novo campo sensível no PDF precisa passar pela mesma regra de escopo da unidade.
- O WhatsApp depende da fila de mensagens (`manage.py enviar_mensagens_pendentes` agendado a cada minuto).

## Testes

`python manage.py test apps.orcamento` (cálculo, validação, telas, situação, versão/omissão e persistência confirmada, PDF, e-mail, WhatsApp, link público, API, permissões e isolamento entre unidades). Rede e e-mail sempre simulados.
