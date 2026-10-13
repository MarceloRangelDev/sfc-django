# apps/dashboard — Painel e usuários online

A página inicial do sistema (rota `index`, a raiz `/`, destino do login) é o **painel de indicadores** de uma unidade (cliente matriz/filial), mais o indicador de **usuários online**.

```text
Usuário → Cliente (unidade) → módulos habilitados + permissões de ver cada módulo → blocos do painel
```

## Princípios

- **Tenant**: todo indicador é de UMA unidade — a do usuário; a matriz escolhe uma filial se tiver `cliente.view_itensfilial` (a unidade da URL fora do escopo é ignorada). Unidades nunca são somadas.
- **Permissões**: cada bloco só é calculado e mostrado se o módulo está **habilitado** para o usuário e ele tem a permissão de **ver** aquele módulo (o mesmo critério das telas). Sem nenhuma permissão, a tela avisa em vez de ficar vazia; usuário sem cliente vê um aviso (nada quebra).
- **Sem regra nova**: `services.indicadores` só chama as funções dos módulos donos dos dados.
- **Estados**: sem dados aparecem valores neutros (R$ 0,00, 0, "sem faturamento informado"), nunca erro nem divisão por zero.

## Período

Seletor: **mês atual** (padrão), **mês anterior**, **últimos 30 dias** e **personalizado** (`periodo()` em `services.py`; período inválido ou incompleto cai no mês atual, sem erro). O período vale para o **resultado** e para os **vencimentos**; saldos, estoque e cadastros mostram a **posição de hoje**.

## Indicadores

| Bloco | Permissão (e módulo habilitado) | Cálculo |
|---|---|---|
| Resultado do período | `financeiro.view_relatoriofinanceiro` | receitas, despesas e resultado por **caixa** (`financeiro.services.dre`); indicador gerencial, não contábil |
| Contas a pagar / a receber | `view_contapagar` / `view_contareceber` | em aberto (qtd e saldo), **atrasadas** e **vencem no período**, de contas ativas com saldo |
| Estoque | `estoque.view_movimentoestoque` | valor a custo (`valor_em_estoque`), itens abaixo do mínimo, lotes vencidos/vencendo |
| Precificações | `precificacao.view_precificacao` | preço de venda médio e **CMV médio** (média simples do CMV % de cada precificação ativa) |
| Cadastros ativos | `insumo.view_insumo`, `produto.view_produto`, `precificacao.view_precificacao` | contagens |
| Categorias com maior faturamento | `categoria.view_categoria` | top 5 por faturamento informado, % do total e ticket médio |
| Usuários online | `auth.view_user` | ver abaixo |

## Usuários online

O deploy é gunicorn síncrono (sem WebSocket), então **online = fez alguma requisição nos últimos 5 minutos** (`presenca.MINUTOS_ONLINE`).

- **Marcação** (`middleware.PresencaMiddleware`, logo após a autenticação): grava `Presenca.ultima_atividade` no máximo **1 vez por minuto por sessão** (o instante da última gravação fica na sessão), então o custo no banco é baixo. A atualização automática do sino de notificações (a cada 20 s, só com a aba visível) conta como atividade. Falhas são logadas e **nunca derrubam a requisição**. Logout remove a presença na hora (sinal `user_logged_out`).
- **Quem aparece** (`usuarios_online`): usuários **ativos** que o solicitante pode ver (`web_project.acesso.usuarios_acessiveis`: a própria estrutura matriz/filiais; Admin: todos; superusuários só para superusuários) e só com `auth.view_user` (sem a permissão, nem o contador aparece; o endpoint responde 403).
- **Dados expostos**: apenas **nome** (ou usuário), **unidade** e **minutos** desde a última atividade — nunca e-mail, IP, id ou página visitada. A lista é carregada ao clicar no contador (`painel/online`, GET JSON) e montada no navegador com `textContent`.
- Banco: `dashboard_presenca` (`usuario_id` único, `ultima_atividade` indexada). Sem histórico: só o instante mais recente.

## Arquivos

| Arquivo | Papel |
|---|---|
| `views.py` | `DashboardView` (rota `index`) e `UsuariosOnlineView` (`dashboard-online`) |
| `services.py` | `periodo`, `indicadores` |
| `presenca.py`, `middleware.py`, `apps.py` | presença: registro, consulta, gravação limitada e logout |
| `templates/dashboard.html` | painel, filtros e modal |
| `models.py` / `migrations/0001_presenca` | `Presenca` |

A raiz `/` deixou de ser a página de exemplo (`apps/sample`): `apps/sample/urls.py` aponta a rota `index` para o painel, e o menu lateral mostra "Painel" (sem o selo de exemplo).

Testes: `apps/dashboard/tests.py` (períodos e viradas de mês/ano, valores de cada bloco, valores neutros, permissões e módulos desabilitados, isolamento entre unidades, seletor de unidade, presença com limite de gravação e tolerância a falhas, escopo/visibilidade e dados mínimos dos usuários online, endpoint, logout).

## Limitações

- "Online" é aproximado (janela de 5 minutos); quem fecha a aba sem sair continua aparecendo até a janela expirar.
- Não há gráficos de série temporal nem comparação entre períodos; o painel mostra totais e listas.
- Categorias usam o faturamento **informado** nelas (não há módulo de vendas), e por isso esse bloco não depende do período.
