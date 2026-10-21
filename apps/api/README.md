# apps/api — API REST v1

## Objetivo

API (para o app mobile) com **as mesmas regras de segurança da interface web**: mesmos serviços, formulários, escopo de cliente/matriz/filial e permissões. Documentação interativa em `/api/docs/` (Swagger).

## Estrutura

| Arquivo | Papel |
|---|---|
| `views.py` | ViewSets (insumos, medidas, categorias, usuários, clientes, módulos, perfis, permissões, auditoria) e autenticação. |
| `serializers.py` | Campos explícitos (sem mass assignment); reaproveita `UsuarioForm`, `Cliente.clean`, `criar_cliente`, `salvar_papel`. |
| `permissions.py` | `PermissaoDoSistema`: autenticado + módulo liberado + permissão nativa do Django por ação. |
| `authentication.py` | Token `Bearer` com expiração. |
| `urls.py`, `pagination.py`, `renderers.py`, `exceptions.py`, `schema.py` | Infraestrutura. |

## Regras de segurança (iguais às telas)

```text
Token → usuário → Cliente → Segmento → Módulo habilitado → permissão do perfil → queryset ESCOPADO
```

| Recurso | Escopo do queryset |
|---|---|
| insumos, categorias | `cliente = cliente do usuário` |
| clientes (e `/clientes/<id>/modulos/`) | `clientes_acessiveis` (própria matriz/filiais; Admin: todos) |
| usuários | `usuarios_acessiveis` |
| perfis | `perfis_visiveis` (Admin oculto para não-Admin) |
| auditoria | `registros_visiveis` |
| notificações | `/api/v1/notificacoes/` (só a caixa do próprio usuário; lidas somem após 48 h); envio só pelas telas (`/notificacoes/...`), sempre escopadas |
| templates do WhatsApp | `/api/v1/whatsapp-templates/` — `conexao__cliente__in=clientes_dos_itens(usuário, ação)`; permissões `integracao.*_templatewhatsapp` |

Trocar `/api/v1/clientes/10/` por `/11/` fora da estrutura responde **404**. `POST /api/v1/clientes/` cria também o usuário **Gestor** (campo `email_gestor`). `POST /api/v1/usuarios/` com perfil Admin fora do cliente do sistema responde **400**. `GET /api/v1/auth/me/` devolve cliente, segmento e a lista de módulos liberados.

## Recursos operacionais (`views_operacao.py`, `serializers_operacao.py`)

Custos comuns (CRUD), movimentações de estoque (consulta e **edição** com recálculo de saldos) e a distribuição do custo operacional por categoria (consulta e ajuste manual) usam os **mesmos serviços das telas** (`apps/estoque/services.py::editar_movimento`, `apps/custo/services.py`). O cliente vem sempre do usuário autenticado; outra empresa recebe 404; as permissões são as nativas do Django por ação (`PermissaoDoSistema`). Valores em dinheiro são inteiros em **centavos**. Erros de regra (saldo negativo, ajuste que não fecha o total) respondem **422**; nome repetido, **409**. A lista completa de rotas está no README principal (seção API) e no Swagger (`/api/docs/`). Testes: `apps/api/tests_operacao.py`.

## Recursos de cadastro, estoque, financeiro e painel (`negocio.py`, `cadastros.py`, `estoque_financeiro.py`)

`negocio.py::RecursoViewSet` é a base dos recursos que têm formulário nas telas (produtos, canais, custos, processados, fichas, precificações,
categorias e contas financeiras): o corpo JSON passa por um serializer de ENTRADA (tipos; dinheiro em centavos), é convertido por
`para_formulario` (1250 → "12,50") e **validado pelo mesmo formulário da tela**, que já aplica escopo por unidade, unicidade e regras de negócio;
a gravação usa os serviços das telas (`salvar_processado`, `salvar_ficha`, `salvar_canal`, `salvar_precificacao`, `salvar_custo`...), que auditam.
O cliente vem sempre do usuário autenticado. Para criar um recurso novo: serializer de saída + de entrada, subclasse de `RecursoViewSet` com
`form_class`, permissões e `dados_atuais`, decorada com `documentar(...)`, e uma linha em `urls.py`. Exclusão (`destroy`): sem uso apaga; em uso apenas desativa.

Erros: 400 (campo inválido), 401, 403, 404 (inclui outra empresa), 422 (regra de negócio: saldo insuficiente, margem + taxas ≥ 100 %, ciclo, baixa acima do saldo).
Testes: `apps/api/tests_cadastros.py`, `tests_estoque_financeiro.py` e `tests_operacao.py`.

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| novo recurso na API | ViewSet + Serializer + `urls.py` (declare `permissao_app`, `permissao_model`, `modulo_slug`) e **filtre o queryset pelo escopo** |
| regra de negócio | no *service*/form compartilhado, nunca só no serializer |

## Novos recursos desta atualização (resumo; detalhes no Swagger, `/api/docs/`)

| Recurso | Endpoints | Observações |
|---|---|---|
| Orçamentos (tag `orcamentos`) | `GET/POST /api/v1/orcamentos/`, `GET/PATCH/PUT/DELETE /{id}/`, `POST /{id}/aprovar|recusar|cancelar/`, `GET /{id}/pdf/`, `GET /{id}/canais/`, `POST /{id}/enviar/` | Permissões `orcamento.*` (+ `enviar_orcamento`; WhatsApp também `integracao.enviar_mensagem`). Totais calculados no servidor; unidade do usuário (outra = 404); fechado = 422. `enviar`: `omitir_itens` só afeta a versão; alterar o salvo exige `persistir_alteracao` **e** `confirmar_persistencia`. PDF em `application/pdf`. |
| Canais de venda | `/api/v1/canais-venda/` (ampliado) | Lista também canais **globais** liberados ao segmento (`eh_global`, `segmentos`, `somente_leitura`); só o administrador do sistema grava `eh_global`/`segmentos`; global de outro dono: PATCH/DELETE = 404. |
| Formas de pagamento | `/api/v1/formas-pagamento/` (ampliado) | Mesma regra de globais. |
| Produtos/serviços | `/api/v1/produtos/` (ampliado) | `tipo` (`produto`/`servico`, só escolhível em Serviço e Produto) e filtro `?tipo=`. |
| Fichas técnicas | `/api/v1/fichas-tecnicas/` (ampliado) | `mao_de_obra: [{colaborador, horas}]` (segmentos Serviço e Serviço e Produto); `itens` opcional nesses segmentos. |
| Clientes | `/api/v1/clientes/` (ampliado) | Criação exige `usuario_nome`, `usuario_username`, `email_gestor` (o responsável recebe link de senha por e-mail); novos campos `endereco`, `cidade`, `uf`, `email_contato`, `whatsapp`. |
| Compromissos (agenda) | `/api/v1/compromissos/` (ampliado) | `concluido` na entrada/saída; PATCH não zera campos omitidos. |
| Catálogo do PDV | `/api/v1/pdv/produtos/` (ampliado) | Paginado (`pagina`, `por_pagina` ≤ 200, padrão 100) em `data.itens` + `data.paginacao`; busca por nome, categoria ou código. |
| Precificações | `/api/v1/precificacoes/` (ampliado) | `motivo_fora_do_pdv` (por que uma precificação marcada para o PDV não aparece). |
