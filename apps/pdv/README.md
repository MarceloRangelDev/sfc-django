# PDV (caixa, vendas, comandas e comprovante)

Ponto de venda integrado ao estoque, ao financeiro e à auditoria. O comprovante é **NÃO FISCAL**; a emissão fiscal é outro fluxo (`apps/fiscal`, Fase 9) e só acontece com provedor configurado e confirmado.

## Como o PDV decide preço e ficha

O PDV vende **produtos** cuja **Precificação** está marcada **"Usar no PDV"** (no máximo uma, ativa, por produto; marcar uma desmarca as outras, com auditoria). Dela vêm o **preço** (`preco_venda`) e a **ficha técnica** usada na baixa de estoque. O PDV é um canal próprio: não depende de `CanalVenda` (que segue opcional). O app/tela só envia *produto + quantidade*; **preço, ficha, totais, taxas e troco são calculados no servidor**. Cada item e pagamento guarda um **snapshot** (nome, preço, ficha, taxa): mudar cadastros depois não altera vendas antigas.

## Perfis

| Ação | Operador | Gestor | Administrador |
|---|---|---|---|
| Vender (balcão/comanda), ver **as próprias** vendas | sim | sim | sim |
| Abrir e fechar o **próprio** caixa (`abrir_caixa`) | sim | sim | sim |
| Ver todas as vendas/caixas da unidade | — | sim | sim |
| Cancelar venda (`cancelar_venda`), devolver itens (`devolver_item`) | — | sim | sim |
| Sangria (`sangria`), suprimento (`suprimento`), conferir fechamento (`conferir_caixa`), conceder desconto (`conceder_desconto`) | — | sim | sim |
| Formas de pagamento, mesas e ajustes (Configurações) | — | ver/criar/editar | tudo |

As permissões próprias do PDV ficam fora da matriz de Papéis; para outros usuários use a permissão especial por usuário. A migration `pdv.0001` cria e concede essas permissões aos perfis já existentes.

## Caixa

Um **turno por operador** (restrição no banco: um caixa aberto por operador), na unidade do operador (matriz ou filial). Abre com o **troco inicial**; **sangria/suprimento** são lançamentos imutáveis (sangria não pode passar do dinheiro disponível). O **dinheiro esperado** = troco inicial + vendas em dinheiro + suprimentos − sangrias − devoluções em dinheiro (vendas canceladas não entram). No **fechamento** o operador informa o dinheiro contado; o sistema guarda esperado, contado e **diferença**, e quem tem `conferir_caixa` **confere** depois. Não fecha com comanda aberta.

## Venda

- **Pagamentos:** de 1 a 10 por venda (formas mistas e **conta dividida**: a tela divide em 2–4 partes iguais, o resto de centavos vai para as primeiras; cada parte aceita um *pagador*). A soma deve ser **exatamente** o total. **Troco** só em dinheiro (`valor_recebido ≥ valor`).
- **Taxa por forma de pagamento:** escolhida em *Configurações > Formas de pagamento*: **sem taxa**, **percentual**, **valor fixo por transação** ou **ambos**. Taxa de um pagamento = `round_half_up(valor × % / 100) + fixa` (nunca passa do valor). Gravada no pagamento (snapshot) e mostrada como líquido nos relatórios do caixa. A taxa é **informativa**: o financeiro registra a receita pelo valor bruto (não gera despesa de taxa).
- **Desconto:** em centavos, até o subtotal, exige `conceder_desconto`; o total precisa ser > 0.
- **Estoque:** a baixa ocorre **uma vez** (`baixa_estoque_em`) na finalização, pelos insumos da ficha técnica × quantidade vendida (PEPS do estoque; movimentos com a observação *Venda PDV #n*). O que saiu fica em `VendaItemInsumo` (rastreabilidade). Saldo insuficiente **recusa a venda inteira** (nada é gravado). O insumo gerado por um Processado é um insumo comum (baixa direta nele; a receita do processado não é reaberta). **Produtos com `controla_estoque`** também têm o saldo próprio baixado (uma vez por item; restrição única no banco) e estornado no cancelamento/devolução — ver `apps/estoque/README.md`. Desligue em *Configurações do PDV* ("Dar baixa no estoque ao vender") para negócios sem controle de insumos.
- **Financeiro:** a venda paga gera uma **conta a receber** (categoria *Vendas PDV*, criada na primeira venda) já **baixada**, uma baixa por pagamento.
- **Numeração:** sequencial por unidade (bloqueio da unidade evita repetição).
- **Idempotência:** `chave` por tentativa (tela: gerada no navegador; API: campo `chave` ou cabeçalho `Idempotency-Key`). Repetir a mesma chave devolve a **mesma venda** (`criada: false`) sem duplicar estoque, conta ou numeração. Se a rede cair, a tela mantém a chave e o reenvio é seguro.
- **Consumidor** opcional (da mesma unidade, ativo e não anonimizado).

## Comandas e mesas

Uma comanda é uma venda **aberta** ligada a uma **mesa** (uma comanda aberta por mesa, garantido no banco). Itens podem ser lançados/removidos enquanto aberta (preço do servidor; mesmo produto soma); nada é baixado nem recebido até **receber** (conta dividida = vários pagamentos). A baixa de estoque da comanda acontece ao receber (na Fase 6, pedidos de cozinha podem baixar na confirmação; a baixa nunca se repete).

## Cancelamento e devolução

- **Cancelar** (venda aberta ou paga, motivo obrigatório): repõe o estoque dos itens **não preparados**, estorna as baixas e inativa a conta a receber. Venda paga só com o **caixa dela ainda aberto**; depois use a devolução. Não cancela venda com devolução registrada.
- **Devolver itens** (venda paga): valor proporcional ao item **com o desconto rateado**; o estoque só é reposto se o responsável pedir **e o item não foi preparado** (`preparado`, marcado pela cozinha na Fase 6; hoje padrão falso). Devolução em dinheiro exige **caixa aberto do usuário** e sai do esperado. Gera uma **conta a pagar** já baixada (*Devoluções de vendas*). Devolução é **imutável**.

## Comprovante (todas as opções de impressão)

`pdv/vendas/<id>/comprovante` — página HTML **imprimível** em qualquer dispositivo (botão *Imprimir* do navegador), com CSS para **80 mm**, **58 mm** e **A4**; `?formato=pdf` (recibo contínuo ou A4); `?formato=escpos` (bytes ESC/POS cp850 para impressora térmica via agente/app de impressão). Mostra *COMPROVANTE NAO FISCAL*. O layout foi gerado e testado por software, **não em hardware de impressão**.

## API (`/api/v1`, mobile)

| Método | Rota | Descrição | Permissão |
|---|---|---|---|
| GET/POST/PUT/PATCH/DELETE | `/formas-pagamento/`, `/{id}/` | Formas e taxa (`taxa_modo`: `sem_taxa`/`percentual`/`fixa`/`ambas`; `taxa_fixa` em centavos) | `pdvconfig.*_formapagamento` |
| GET/POST/... | `/mesas/`, `/{id}/` | Mesas | `pdvconfig.*_mesa` |
| GET/PATCH | `/pdv/configuracao/` | Ajustes do PDV da unidade | `pdvconfig.view/change_configuracaopdv` |
| GET | `/pdv/produtos/?q=` | Produtos vendáveis e preço | `pdv.add_venda` |
| GET | `/pdv/caixa/atual/`, `/pdv/caixa/`, `/pdv/caixa/{id}/` | Caixa aberto, histórico e detalhe (com resumo) | `pdv.view_caixa` |
| POST | `/pdv/caixa/abrir/`, `/fechar/`, `/sangria/`, `/suprimento/`, `/{id}/conferir/` | Operações de caixa | `abrir_caixa`, `sangria`, `suprimento`, `conferir_caixa` |
| GET/POST | `/pdv/vendas/`, `/pdv/vendas/{id}/` | Lista (`status`, `q`), cria venda de balcão (**idempotente**), detalhe | `view_venda` / `add_venda` |
| POST | `/pdv/vendas/{id}/cancelar/`, `/pdv/vendas/{id}/itens/{item}/devolver/` | Cancelar e devolver | `cancelar_venda`, `devolver_item` |
| GET | `/pdv/vendas/{id}/comprovante/?formato=json\|pdf\|escpos&tamanho=` | Comprovante | `view_venda` |
| GET/POST | `/pdv/comandas/`, `/pdv/comandas/{id}/` | Comandas abertas / abrir / detalhe | `view_venda` / `add_venda` |
| POST/DELETE | `/pdv/comandas/{id}/itens/`, `/itens/{item}/` | Lançar/remover item | `add_venda` |
| POST | `/pdv/comandas/{id}/pagar/` | Receber (conta dividida; idempotente) | `add_venda` |

Erros: 400 validação/caixa não aberto, 401, 403, 404 (outra unidade ou venda de outro operador), 422 regra de negócio (soma dos pagamentos, estoque, caixa fechado...). Dinheiro em **centavos**. Atenção ao `API_THROTTLE_USER` (padrão 2000/h) para um caixa movimentado.

## Tenant e auditoria

Todo acesso passa por `clientes_dos_itens` (unidade do usuário e, para a matriz com `cliente.*_itensfilial`, as filiais); quem não tem `cancelar_venda` só enxerga **as próprias** vendas. Audita-se: abertura/fechamento/conferência de caixa, sangria/suprimento, criação/recebimento da venda (com itens, pagamentos e totais), lançamento/remoção de itens da comanda, cancelamento (motivo), devolução, criação das contas e categorias financeiras automáticas e as configurações.

## Banco de dados

Migrations `pdv.0001`/`0002` (tabelas novas; concedem permissões), `pdvconfig.0001` (tabelas novas), `precificacao.0006` (coluna `usar_no_pdv`) e `modulo.0014` (módulos `pdv`, `forma-pagamento`, `mesa`, `pdv-config`). Nenhum dado existente é alterado.

| Tabela | Colunas principais |
|---|---|
| `pdv_caixa` | `cliente_id` FK, `operador_id` FK, `status` (aberto/fechado; único aberto por operador), `valor_abertura`, `aberto_em`, `fechado_em` (nulo), `valor_esperado`/`valor_contado`/`diferenca` (nulos até fechar), `observacao_fechamento`, `conferido_por_id`/`conferido_em` (nulos), `observacao_conferencia` |
| `pdv_movimentocaixa` | `caixa_id`, `tipo` (sangria/suprimento), `valor`, `motivo`, `usuario_id`, `criado_em` — **imutável** |
| `pdv_venda` | `cliente_id`, `numero` (único por unidade), `caixa_id`, `consumidor_id` (nulo), `mesa_id` (nulo; única aberta por mesa), `origem`, `status`, `subtotal`, `desconto` (≤ subtotal), `total`, `troco`, `chave_idempotencia` (nula; única por unidade), `baixa_estoque_em`, `conta_receber_id` (nulo), `observacao`, `criado_por_id`, `finalizada_em`, `cancelada_em`/`cancelada_por_id`/`motivo_cancelamento` |
| `pdv_vendaitem` | `venda_id`, `produto_id`, `ficha_id`, `descricao`, `quantidade` decimal(12,3), `preco_unitario`, `total`, `quantidade_devolvida` (≤ quantidade), `preparado`, `observacao` |
| `pdv_vendapagamento` | `venda_id`, `forma_id`, `forma_nome`, `forma_tipo`, `valor`, `valor_recebido` (nulo), `taxa_valor`, `taxa_descricao`, `pagador` |
| `pdv_vendaiteminsumo` | `item_id`, `insumo_id`, `medida_id`, `quantidade` baixada, `quantidade_reposta` (≤ baixada) |
| `pdv_devolucao` | `venda_id`, `item_id`, `quantidade`, `valor`, `repor_estoque`, `forma_estorno_id`, `caixa_id` (nulo), `motivo`, `usuario_id`, `conta_pagar_id` (nulo), `criado_em` — **imutável** |
| `precificacao_precificacao.usar_no_pdv` | bool, padrão falso; único por produto quando `usar_no_pdv` e `ativo` |

Valores em centavos (bigint). Índices em (`cliente`, `status`) de caixa e venda e em `venda.caixa`.


## Catálogo do PDV: nada some em silêncio (atualizado)

O catálogo (`services.produtos_do_pdv`) mostra precificações **marcadas para o PDV, ativas, de produto e ficha ativos e com preço > 0**, da unidade do caixa. Causas de “marquei e não aparece” investigadas: (1) a tela cortava em **80 produtos sem avisar** — agora mostra até 300 e avisa “Mostrando X de Y” (`truncado`/`total` no JSON); a API do catálogo (`/api/v1/pdv/produtos/`) passou a ser **paginada** (`?pagina=&por_pagina=`, padrão 100, máx. 200; a lista continua em `data.itens`); (2) a busca só olhava o nome — agora também a **categoria** e o código; (3) a lista de **Precificações** não mostrava o status do PDV — ganhou a coluna *PDV* (“No PDV” ou “Marcada, mas fora do PDV” + motivo: precificação/produto/ficha inativos ou preço zero; também em `motivo_fora_do_pdv` na API). Nenhuma regra de autorização foi enfraquecida (continua só a unidade do caixa).

**Formas de pagamento globais** liberadas ao segmento também valem no pagamento, na devolução e na tela (ver `apps/pdvconfig/README.md`). **Serviços** (ficha só com mão de obra) vendem sem baixar estoque.

---

# Atualização 6 — autorização, estoque, nota fiscal, cozinha em tempo real e ordem do menu

Esta seção documenta o conjunto de mudanças da atualização “PDV + Pedidos e cozinha + Configurações”. O que não aparece aqui continua como descrito acima.

## Objetivo e limites

Reforçar o PDV (autorização de operações sensíveis, estoque visível, pergunta de nota fiscal), fechar o ciclo PDV → cozinha → Vendas e permitir ao administrador reordenar o menu de cada cliente. **Não** cria integração fiscal nova (reaproveita `apps/fiscal`), **não** muda cálculo de preço, estoque ou financeiro. Módulos envolvidos: `pdv` (este), `pedido` (cozinha), `fiscal`, `modulo` (menu), `tour`, `api`.

## Fluxos principais

| Fluxo | Como funciona | Arquivos |
|---|---|---|
| **Desconto** (balcão, comanda e API) | Qualquer desconto > 0 exige credencial (senha ou código de crachá) de quem tem `pdv.conceder_desconto`, **mesmo que o operador já tenha a permissão**. A tela pede ao finalizar; o servidor valida *antes* de gravar a venda. O autorizador e o valor vão para a auditoria. | `autorizacao.exigir_desconto`, `views.FinalizarJsonView`, `views.ComandaPagarJsonView`, `src/assets/js/pdv.js` |
| **Limpar** | O botão mostra o que será descartado (itens, quantidade, total, desconto) e exige credencial (`pdv.remover_item`). Venda vazia não pede nada. A auditoria registra o resumo. | `views.AutorizarJsonView` (`acao: "limpar"`), `pdv.js` |
| **Cancelar venda/comanda** | Já exigia senha; agora aceita também o código de crachá (`codigo_autorizacao`). Na API passou a exigir `autorizacao` (ver “Compatibilidade”). | `views._autorizar_pelo_post`, `api/pdv.py::VendaViewSet.cancelar` |
| **Emissão fiscal** | Ao finalizar, se a empresa emite nota, o módulo `fiscal` está habilitado, há tipo habilitado e o usuário tem `fiscal.emitir_nota`, a resposta traz `nota_fiscal` e a tela pergunta “Emitir nota?”. “Sim” chama `fiscal.services.solicitar_emissao` (idempotente). Sem provedor o documento fica **pendente** com o motivo; nada é simulado. | `views.oferta_fiscal`, `views.NotaVendaJsonView`, `pdv.js::perguntarNota` |
| **Estoque visível** | Cada produto do catálogo e da comanda mostra o saldo (`estoque.rotulo`) ou “Estoque não controlado”. Só produtos com `controla_estoque` têm saldo próprio; o saldo vem no mesmo `select_related` (sem N+1). | `services.estoque_do_produto`, `produtos_do_pdv` |
| **Comanda → cozinha** | Cada item da comanda aparece como **Novo** ou **Enviado #N**; o botão mostra quantos itens novos há. Enviar cria um pedido só com os novos (idempotente por `pedido_item`). | `views.ComandaView`, `pedido/views.EnviarCozinhaView` |
| **Entrega em Vendas** | A lista e o detalhe da venda mostram “Entregue” quando todos os pedidos não cancelados foram finalizados (“Em andamento” se ainda há pedido na cozinha). Derivado, sem coluna. | `Venda.entrega`, templates `pdv_vendas.html`, `pdv_venda.html` |

## Código de crachá (barras/QR)

Quem autoriza gera o próprio código em **PDV > Meu caixa > Código de autorização** (ou `POST /api/v1/pdv/autorizacoes/codigo/`). O código (16 hex) aparece **uma vez**; gerar outro invalida o anterior. Só o HMAC-SHA256 (chave = `SECRET_KEY`) é guardado em `pdv_codigoautorizacao.codigo_hash`. O leitor “digita” o código no campo do SweetAlert e confirma com Enter. Senha e código compartilham o mesmo limite de tentativas (5 erros em 15 minutos por usuário logado, mensagem genérica).

## Regras de autorização, tenant e auditoria

- O autorizador precisa estar ativo, ter a permissão da operação **e** alcançar a unidade da venda (`clientes_acessiveis`) ou ser administrador global: supervisor/crachá de outro tenant é recusado.
- A credencial nunca é gravada nem logada; a auditoria diz “Autorizado por <usuário>” + a ação (+ resumo no limpar).
- Todos os endpoints novos resolvem a unidade da sessão/token (`_venda`, `clientes_dos_itens`); venda/documento de outra unidade = 404.
- O front só coleta; o servidor revalida tudo (permissão, escopo, estado da venda, limites do desconto).
- Proteção contra duplicidade: venda e comanda por `chave` (a credencial do desconto é reaproveitada no reenvio por falha de rede); nota fiscal por `(venda, tipo)` ativo; pedido por `pedido_item` da linha da comanda.

## Endpoints

Web (sessão + CSRF; tela):

| Método | Rota | Finalidade | Autorização / tenant | Entrada → saída |
|---|---|---|---|---|
| POST | `/pdv/api/finalizar` | Venda de balcão | módulo + `add_venda`; unidade do caixa aberto | **novo** `autorizacao:{usuario?,senha?,codigo?}` obrigatória com `desconto>0`; saída ganha `nota_fiscal` (`tipos`,`url`) ou `null`; 422 sem credencial |
| POST | `/pdv/comandas/<id>/pagar` | Receber comanda | idem; venda da unidade | idem |
| POST | `/pdv/api/autorizar` | Valida credencial de `limpar`/`remover_item` | unidade do usuário; perm. do autorizador `remover_item` | `{acao, usuario?, senha?\|codigo?, resumo?}` → 200 / 403 genérico |
| POST | `/pdv/api/codigo-autorizacao` | Gera o crachá do próprio usuário | qualquer permissão de autorização | → `{codigo}` (uma vez) |
| POST | `/pdv/api/vendas/<id>/nota` | “Emitir nota?” | `view_venda` + `fiscal.emitir_nota` + módulo fiscal; venda da unidade (404) | `{tipo}` → `{criado,status,motivo,documento}`; 422 tipo inválido/indisponível |
| GET | `/pdv/api/produtos` | Catálogo | caixa aberto | itens ganham `estoque:{controlado,saldo,rotulo}` |
| GET | `/cozinha/api/eventos` | Push da tela (SSE) | módulo `pedido` + `view_pedido`; unidades da sessão | `text/event-stream` |
| POST | `/modulos/ordem` | Ordem do menu do cliente | `change_clientemodulo`; cliente em `clientes_acessiveis` (404) | `{cliente_id, modulos:[ids]}` ou `{cliente_id, restaurar:true}` |

API v1 (token; documentada no Swagger `/api/docs/`, tags `pdv`, `pedidos`):

| Método | Rota | Situação | Observação |
|---|---|---|---|
| POST | `/pdv/vendas/` | **alterado** | `autorizacao` obrigatória com `desconto>0` (antes bastava a permissão de quem chamava) |
| POST | `/pdv/comandas/{id}/pagar/` | **alterado** | idem |
| POST | `/pdv/vendas/{id}/cancelar/` | **alterado (incompatível, ver abaixo)** | exige `autorizacao` de quem tem `cancelar_venda`; a permissão exigida de quem chama passou a `view_venda` (o operador cancela com o supervisor, como na tela) |
| GET | `/pdv/produtos/` | ampliado | `estoque` por produto |
| GET | `/pdv/vendas/`, `/{id}/` | ampliado | `entrega` (`""`, `em_andamento`, `entregue`) |
| GET | `/pedidos/fila/` | ampliado | passa a incluir pedidos **novos** (antes ocultos) |
| POST | `/pdv/autorizacoes/validar/` | **novo** | equivalente mobile do “Limpar”/excluir item |
| POST | `/pdv/autorizacoes/codigo/` | **novo** | gera o crachá |

**Reutilizado / considerado desnecessário:** emissão de nota no mobile usa o existente `POST /api/v1/fiscal/documentos/` (e `GET /fiscal/configuracao/` para a prontidão): não foi criado endpoint fiscal novo. O push da cozinha é SSE de sessão (EventSource não envia `Authorization`); o app mobile deve usar `GET /pedidos/fila/` por polling leve ou, no futuro, Web Push — não foi criado canal de token por falta de caso de uso comprovado. O PDV já tinha endpoints de mesas, formas e caixa suficientes.

**Compatibilidade:** mudança incompatível em `cancelar` e `desconto` (API v1 sem consumidores publicados até aqui; os apps futuros devem enviar `autorizacao`). Erros de credencial = 422 com mensagem genérica; não há versionamento paralelo.

## Eventos / push (Pedidos e cozinha)

- **Origem:** qualquer pedido que nasça ou mude de estado (envio da comanda, API, mover na cozinha) altera `pedido_pedido.atualizado_em`/contagem.
- **Mecanismo:** `GET /cozinha/api/eventos` (SSE). A conexão dura 45 s; a cada 2 s o servidor compara a marca `versao_da_fila(unidades)` (contagem + último `atualizado_em`) e emite `event: fila` com `{"v": "<marca>"}` quando muda (ou `: ping`). O navegador reconecta sozinho. Como a marca vem do **banco**, funciona com vários processos, sem Redis.
- **Destinatários:** só quem tem o módulo `pedido` e `view_pedido`; as unidades vêm da sessão (`clientes_dos_itens`), nunca de parâmetro. **Payload mínimo:** só a marca, sem dados de pedido; a tela então busca `/cozinha/api/fila`, que reaplica a autorização.
- **Interface:** `cozinha.js` recarrega a fila quando a marca muda (mantém uma consulta lenta de 30 s como rede de segurança); `pedidos.html` recarrega a lista. **Deploy:** SSE ocupa uma thread por tela aberta; por isso `gunicorn-cfg.py` passou a `worker_class = gthread`, `threads = 16`. O endpoint envia `X-Accel-Buffering: no` (nginx).

## Tours

Já tinham tour: Insumos, Clientes, Categorias e as chaves do registro `src/assets/js/tours-modulos.js` (ver `apps/tour/README.md`). **Adicionado:** `cozinha` (Pedidos e cozinha) — era a única tela de módulo sem tour. **Atualizados** (passos novos/textos corrigidos): `pdv-venda` (estoque, desconto, Limpar), `pdv-comanda` (estoque, Novo/Enviado, desconto autorizado), `pdv-caixa` (crachá), `pdv-vendas` (selo Entregue), `pdv-venda-detalhe` (cancelamento com autorização), `modulo-cliente` (ordem do menu). Sem tour por serem páginas não-fluxo: impressão (`pdv_comprovante`), erro/manutenção, login/registro e o modelo `sample`. Para criar um tour: adicionar a chave em `TOURS`, o par em `apps/tour/tests_modulos.py::TELAS` e `<script>initTourModulo('chave');</script>` no `page_js`.

## Banco de dados

Duas tabelas novas (migrações aditivas e reversíveis; nenhuma tabela existente foi alterada).

### `pdv_codigoautorizacao` (migração `pdv.0005_codigo_autorizacao`)

Finalidade: código de crachá do autorizador. Escopo: por usuário (o escopo de unidade é validado no uso, pelo vínculo do usuário com a unidade da venda).

| Coluna | Tipo | Nulo | Padrão | Chaves / índices |
|---|---|---|---|---|
| `id` | bigint | não | autoincremento | PK |
| `usuario_id` | integer | não | — | FK `auth_user` (CASCADE), **único** (1 código por usuário) |
| `codigo_hash` | varchar(64) | não | — | **único** (HMAC-SHA256 hex; nunca o código) |
| `criado_em` | datetime | não | agora | — |

Relação: `User.codigo_autorizacao` (1:1). Impacto nos dados: nenhum; usuários sem código simplesmente autorizam por senha. Reverter: `manage.py migrate pdv 0004` (apaga a tabela e os crachás; basta gerar de novo).

### `modulo_ordemmenucliente` (migração `modulo.0025_ordem_menu_cliente`)

Finalidade: ordem personalizada de um módulo no menu de um cliente. Escopo: **por cliente (tenant)**. Sem linha vale `modulo_modulo.ordem` (padrão preservado).

| Coluna | Tipo | Nulo | Padrão | Chaves / índices |
|---|---|---|---|---|
| `id` | bigint | não | autoincremento | PK |
| `cliente_id` | bigint | não | — | FK `cliente_cliente` (CASCADE), índice |
| `modulo_id` | bigint | não | — | FK `modulo_modulo` (CASCADE), índice |
| `ordem` | smallint ≥ 0 | não | — | — |
| `atualizado_em` | datetime | não | auto | — |

Restrição: `ordem_menu_unica_por_cliente_modulo` (único `cliente`+`modulo`). Os valores gravados reaproveitam os “lugares” (`Modulo.ordem`) dos módulos reordenáveis do cliente, intercalando-se corretamente com os não reordenáveis. Voltar à ordem padrão apaga as linhas. Reverter: `manage.py migrate modulo 0024` (menu volta ao padrão para todos).

**Sem mudança de esquema:** entrega em Vendas (derivada de `pedido_pedido.status`), estoque visível (`estoque_saldoproduto`), nota fiscal (`fiscal_documentofiscal` já existe) e fila com pedidos novos.

## Migrações e execução

```bash
.venv/Scripts/python.exe manage.py migrate        # aplica pdv.0005 e modulo.0025
```

Sem seeds novos. Em produção com gunicorn, reinicie para carregar `gthread` (o SSE exige). Nenhum segredo novo: o HMAC usa a `SECRET_KEY` já existente (trocá-la invalida os crachás emitidos; gere de novo).

## Trechos mais complexos

| Trecho | Por que é complexo | Invariantes | Referência |
|---|---|---|---|
| Autorização (`autorizar`/`exigir_desconto`) | Mistura senha e crachá, escopo de unidade, limite de tentativas e auditoria sem vazar credencial. | A credencial nunca é gravada/logada; erro sempre genérico; o autorizador alcança a unidade da venda; desconto sem credencial nunca chega ao serviço; falha conta no limite. | `apps/pdv/autorizacao.py` |
| Desconto no `pdv.js` | Credencial pedida no meio do envio idempotente (chave) e reaproveitada no reenvio por rede. | Mesma `chave` ⇒ mesma venda; credencial descartada quando o servidor recusa; nada de valor decidido no navegador. | `src/assets/js/pdv.js` (`CredencialDesconto`) |
| SSE da cozinha | Conexão longa em servidor com poucas threads, multiprocesso e multi-tenant. | Unidades fixadas na abertura pela sessão; evento só carrega marca; duração limitada (45 s); marca derivada do banco; `gthread` configurado. | `pedido/views.EventosFilaView`, `pedido/services.versao_da_fila`, `gunicorn-cfg.py` |
| Itens novos × enviados | Um item só é “enviado” se tiver `pedido_item`; somar quantidade numa linha já enviada duplicaria o envio. | `adicionar_item` só soma em linha **sem** pedido; `criar_pedido` roda em transação com a venda travada e idempotente por `pedido_item`. | `pdv/services.adicionar_item`, `pedido/services.criar_pedido` |
| Entrega derivada | Venda × pedidos cancelados/parciais. | Entregue = todos os pedidos **não cancelados** entregues (≥1); sem pedido = vazio. Usar `prefetch_related("pedidos")` em listas. | `Venda.entrega` |
| Ordem do menu | Reordenar sem bagunçar módulos não reordenáveis e sem aceitar ids alheios. | A lista enviada é **exatamente** o conjunto reordenável do cliente; valores reaproveitam os “lugares” existentes; padrão = ausência de linhas; cliente resolvido por `clientes_acessiveis`. | `modulo/services.salvar_ordem_do_menu`, `web_project/menu.py` |

## Limitações e decisões

- “Nenhum item novo…”: não foi possível reproduzir uma falha de seleção no servidor (a rotina já era idempotente e coberta por testes). A causa provável é a comanda **já ter sido enviada** (por outra tela ou pela API) sem que a tela dissesse isso; por isso cada item agora mostra **Novo/Enviado #N**, o botão exibe a contagem e a mensagem explica o motivo (comanda vazia × tudo enviado).
- A ordem do menu cobre os módulos do menu principal; o grupo PDV e Configurações mantêm a posição padrão.
- Não há QR gerado como imagem: o código é texto para imprimir com qualquer gerador de barras/QR.
- SSE só na sessão web; mobile usa a fila por polling (documentado acima).
- Nota fiscal: o fluxo cria o documento pendente; a autorização real depende do provedor fiscal configurado (ver `apps/fiscal/README.md`).

## Testes e validações executados (atualização 6)

Novos arquivos: `apps/pdv/tests_autorizacao.py` (crachá, desconto na tela/comanda/API, limpar, cancelar com código, nota fiscal, estoque visível, tenant), `apps/pedido/tests_push.py` (fila com novos, Finalizar, entrega em Vendas, mensagens do envio, SSE e marca por unidade), `apps/modulo/tests_ordem.py` (ordem do menu: persistência, efeito no menu, validação, tenant, permissão); ajustes em `apps/pdv/tests_api.py` (cancelar/desconto passam a exigir `autorizacao`) e `apps/tour/tests_modulos.py` (tela `cozinha`).

```bash
.venv/Scripts/python.exe manage.py test apps.pdv apps.pedido apps.modulo apps.tour apps.fiscal   # subconjunto afetado
.venv/Scripts/python.exe manage.py test --noinput                                               # suíte completa (~40 min)
.venv/Scripts/python.exe manage.py makemigrations --check                                       # só acusa a pendência do pacote de terceiros `webpush`
.venv/Scripts/python.exe manage.py spectacular --file schema.yml                                # Swagger gera (avisos antigos de tipo de path)
```

Resultado: subconjunto novo/alterado (`pdv.tests_api`, `pdv.tests_autorizacao`, `pedido.tests_push`, `modulo.tests_ordem`, `tour`): 66 testes, OK. Suíte completa: 1685 testes, 2 falhas **não relacionadas a esta atualização**: `apps.imagens.tests.PainelEApiTests.test_insumo_nao_envia_foto_propria_a_foto_vem_da_marca` (espera um texto que nenhum template de insumo contém; reproduz isolada) e `apps.estoque.tests_etiquetas.EtiquetaWebTests.test_qr_aponta_para_url_com_uuid_sem_dados_sensiveis` (intermitente: o teste procura `/i/1` e falha quando o UUID sorteado começa com “1”; passou ao rodar de novo). **Não verificado em navegador:** a interação visual (arrastar e soltar, SweetAlerts, SSE real num gunicorn `gthread`); o JS foi checado só com `node --check` e o comportamento de servidor pelos testes.

