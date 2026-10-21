# Configurações do PDV

Cadastros de **Configurações** usados pelo PDV (`apps/pdv`): formas de pagamento com taxa, mesas e ajustes da unidade. Acesso: Administrador (tudo) e Gestor (ver/criar/editar); Operador não acessa. Cada unidade (matriz/filial) tem os seus; a matriz alcança as filiais com `cliente.*_itensfilial`.

## Formas de pagamento e taxa

Cada forma escolhe **como cobra a taxa** (`taxa_modo`):

| Modo | Campos usados | Taxa de uma transação |
|---|---|---|
| `sem_taxa` | — | 0 |
| `percentual` | `taxa_percentual` (> 0 e < 100) | `round_half_up(valor × % / 100)` |
| `fixa` | `taxa_fixa` (centavos, > 0) | valor fixo **por transação** (por pagamento lançado) |
| `ambas` | os dois | percentual **+** fixa |

O modo manda: o campo que ele não usa é **zerado** no servidor; os que usa precisam de valor. A taxa nunca passa do valor pago. É gravada em cada pagamento da venda (snapshot) — alterar a forma depois **não muda vendas antigas**. O `tipo` (dinheiro, pix, débito, crédito, outra) define o comportamento: só **dinheiro** gera troco e entra no esperado do caixa. O botão *Criar formas padrão* cria Dinheiro, PIX, Débito e Crédito (sem taxa) sem duplicar.

Exemplo: crédito 3% + R$ 0,50 sobre R$ 10,00 → taxa R$ 0,80, líquido R$ 9,20.

## Mesas

Nome único por unidade; ativa/inativa (em uso só inativa). Uma mesa tem no máximo uma comanda aberta.

## Ajustes do PDV (por unidade)

`controla_estoque` (padrão ligado: venda baixa o estoque; desligado: não mexe), `formato_comprovante` (`termica80`, `termica58`, `a4`) e `rodape_comprovante` (até 120). A linha da unidade é criada com os padrões na primeira leitura (não auditada) e as alterações são auditadas.

## API e telas

Telas: `formas-pagamento`, `mesas`, `pdv-config`. API: `/api/v1/formas-pagamento/`, `/api/v1/mesas/`, `/api/v1/pdv/configuracao/` (ver `apps/pdv/README.md`).

## Banco de dados (`pdvconfig.0001_pdv`, tabelas novas)

**`pdvconfig_formapagamento`**: `id`; `cliente_id` FK (PROTECT); `nome` varchar(60) (único por unidade); `tipo` varchar(10) (padrão `outro`); `taxa_modo` varchar(10) (padrão `sem_taxa`); `taxa_percentual` decimal(7,4) (padrão 0); `taxa_fixa` bigint centavos (padrão 0); `ativo` (padrão verdadeiro); datas. Índice (`cliente_id`, `ativo`).

**`pdvconfig_mesa`**: `id`; `cliente_id` FK; `nome` varchar(40) (único por unidade); `ativo`; datas.

**`pdvconfig_configuracaopdv`**: `id`; `cliente_id` OneToOne (CASCADE); `controla_estoque` bool (verdadeiro); `formato_comprovante` varchar(10) (`termica80`); `rodape_comprovante` varchar(120) (`''`); `atualizado_em`.

Impacto em dados existentes: nenhum.

## Escopo GLOBAL (liberado por segmento)

Além das formas de cada unidade, o **administrador do sistema** pode cadastrar um canal **global**, disponível para uso por todos os clientes dos **segmentos** escolhidos. Regras (código comum em `web_project/globais.py`):

- O item global continua tendo dono (`cliente` = a unidade do administrador que o cadastrou) e ganha `eh_global` + a lista `segmentos`. Quem não é o dono não alcança o item pelo escopo normal de edição (`clientes_dos_itens`): editar/excluir um global de outro dono responde **404**; o dono que não é administrador também **não consegue salvar alteração** (formulário recusa).
- **Visibilidade para uso** é outra regra, só de leitura: o item aparece para o cliente C se `item.cliente == C` **ou** (`item.eh_global` e o segmento de C está em `item.segmentos`). Vale nas listas (selo “Global”, sem botão de editar), nas escolhas da Precificação e na API (`somente_leitura: true`).
- Os campos `eh_global`/`segmentos` só existem no formulário/API para o administrador do sistema (superusuário ou grupo Administrador, com uma unidade); para os demais são removidos (valor enviado à mão é ignorado). Global exige ao menos um segmento.
- Tirar um segmento **não apaga** o que já foi feito (precificações e vendas continuam); só deixa de poder ser escolhido de novo. Alterar as taxas de um forma de pagamento global refaz as precificações de **todos** os clientes que o usam (`recalcular_do_canal`); se alguma ficar inviável, a edição é recusada.
- Migração `0002_escopo_global`: `eh_global` (bool, padrão falso, `db_default`) e a tabela M2M `*_segmentos`. Itens existentes ficam **não globais** (nada muda).

Tabelas: `pdvconfig_formapagamento` ganha `eh_global` (boolean, NOT NULL, padrão `false`) e `pdvconfig_formapagamento_segmentos` (`formapagamento_id` + `segmento_id`, único por par). Migração `pdvconfig.0003_escopo_global`, reversível.
