# apps/canal — Canais de venda (Configurações > Canal de vendas)

Cadastro dos canais onde uma unidade (cliente matriz/filial) vende (iFood, balcão, WhatsApp…) e das **taxas** que cada um cobra. A Precificação usa estes totais para calcular o preço de venda.

```text
Usuário → Cliente → módulo "canal-venda" (Configurações, essencial: só depende da permissão) → canal.<ação>_canalvenda → CanalVenda (escopo de unidades)
```

## Exemplo de referência

`IFOOD | MOTOBOY PLATAFORMA - com antecipação | 27,79 | 72,21 | 26,20% + 1,59%`

| Coluna da tela | Origem |
|---|---|
| Canal | `CanalVenda.nome` ("IFOOD") |
| Descrição | `CanalVenda.descricao` |
| Total de taxas | `taxa_percentual_total` (26,20 + 1,59 = **27,79%**), mais `taxa_fixa_total` se houver taxas em reais |
| Líquido após taxas | 100 − `taxa_percentual_total` = **72,21%** (propriedade `liquido_percentual`) |
| Taxas (discriminadas) | `descricao_das_taxas`, montada das linhas de `CanalTaxa`: `26,20% (Comissão) + 1,59% (Antecipação)` |

O "líquido" é calculado só sobre as taxas **percentuais** (incidem sobre o preço). Taxas **fixas** são valores em reais por venda e entram na precificação como custo adicional — nunca são somadas aos percentuais.

## Modelagem (sem ambiguidade entre fixa e percentual)

- `canal_canalvenda`: `id`, `cliente_id` (FK PROTECT), `nome` varchar(150), `descricao` varchar(255), `taxa_percentual_total` decimal(7,4) e `taxa_fixa_total` bigint em **centavos** (ambos **calculados** na gravação por `services.salvar_canal`, nunca digitados), `ativo`, `criado_em`, `atualizado_em`. Único `(cliente, nome, descricao)`; índice `(cliente, ativo)`.
- `canal_canaltaxa`: `id`, `canal_id` (FK CASCADE — a taxa é parte do canal), `descricao` varchar(100), `percentual` decimal(7,4) nulo, `valor_fixo` bigint (centavos) nulo. **CheckConstraint `canal_taxa_percentual_ou_fixa`**: exatamente um dos dois preenchido. O formulário valida o mesmo.
- Não há exclusão: canal usado em precificação não pode sumir; use **Inativo** (some das listas padrão e, nas próximas fases, do seletor da Precificação).

## Regras

- Soma dos percentuais **< 100 %** (senão o líquido seria ≤ 0 e a fórmula do preço não teria resultado); validada no serviço e na tela.
- Canal sem taxas é válido (totais zero, líquido 100 %).
- Gravação (canal + taxas substituídas + totais + auditoria) é **atômica**.
- Escopo: criação sempre na unidade do usuário; a lista mostra matriz e filiais (selo + filtro de unidade) quando o usuário tem `cliente.view_itensfilial`; editar filial exige `cliente.change_itensfilial` (ver `apps/acl/README.md`).

## Telas

`canais-venda` (lista paginada: pesquisa, status, unidade, ordenação), `canais-venda/novo` (**Salvar** e **Salvar e adicionar outro**), `canais-venda/<id>/editar`. Taxas são linhas dinâmicas no formulário (adicionar/remover). Menu: Configurações > Canal de vendas (registro no catálogo `seed_modulos.MODULOS_PADRAO["canal-venda"]` e migration `modulo.0005`).

## Permissões padrão

`seed_acl`: Administrador (todas) e Gestor (ver/criar/editar). Rode `python manage.py seed_acl` em bancos existentes.

Testes: `apps/canal/tests.py` (exemplo iFood, fixas × percentuais, limite de 100 %, constraint do banco, duplicidade, auditoria, filtros, permissões, multi-tenant).

## Precificação

Editar as taxas de um canal **reprecifica** (`apps/precificacao/services.py::recalcular_do_canal`, na mesma transação) as precificações que o usam. Se as novas taxas inviabilizarem alguma (margem + taxas ≥ 100 %), a edição é recusada e nada é gravado. Canal usado por precificação não pode ser excluído (só inativado).

## Escopo GLOBAL (liberado por segmento)

Além do canal de cada unidade, o **administrador do sistema** pode cadastrar um canal **global**, disponível para uso por todos os clientes dos **segmentos** escolhidos. Regras (código comum em `web_project/globais.py`):

- O item global continua tendo dono (`cliente` = a unidade do administrador que o cadastrou) e ganha `eh_global` + a lista `segmentos`. Quem não é o dono não alcança o item pelo escopo normal de edição (`clientes_dos_itens`): editar/excluir um global de outro dono responde **404**; o dono que não é administrador também **não consegue salvar alteração** (formulário recusa).
- **Visibilidade para uso** é outra regra, só de leitura: o item aparece para o cliente C se `item.cliente == C` **ou** (`item.eh_global` e o segmento de C está em `item.segmentos`). Vale nas listas (selo “Global”, sem botão de editar), nas escolhas da Precificação e na API (`somente_leitura: true`).
- Os campos `eh_global`/`segmentos` só existem no formulário/API para o administrador do sistema (superusuário ou grupo Administrador, com uma unidade); para os demais são removidos (valor enviado à mão é ignorado). Global exige ao menos um segmento.
- Tirar um segmento **não apaga** o que já foi feito (precificações e vendas continuam); só deixa de poder ser escolhido de novo. Alterar as taxas de um canal global refaz as precificações de **todos** os clientes que o usam (`recalcular_do_canal`); se alguma ficar inviável, a edição é recusada.
- Migração `0002_escopo_global`: `eh_global` (bool, padrão falso, `db_default`) e a tabela M2M `*_segmentos`. Itens existentes ficam **não globais** (nada muda).

Tabelas: `canal_canalvenda` ganha `eh_global` (boolean, NOT NULL, padrão `false`) e `canal_canalvenda_segmentos` (`canalvenda_id` + `segmento_id`, único por par; FKs para `canal_canalvenda` e `modulo_segmento`). Migração `canal.0002_escopo_global`, reversível.
