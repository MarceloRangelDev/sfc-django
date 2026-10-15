# Cotações

Cotação de preços entre lojas: os **itens** vêm da lista de compras (`apps/compras`) ou são digitados (inclusive **serviços**); cada **loja** informa preço por item; o sistema aplica a regra de atacado, **compara** por preço normalizado, o usuário escolhe os **vencedores**, fecha a cotação e pode **gerar contas a pagar**. Exporta em PDF (também serve para imprimir) e Excel.

## Modelo

| Tabela | Papel |
|---|---|
| `cotacao_cotacao` | A cotação (unidade dona, título, observações gerais, status) |
| `cotacao_cotacaoloja` | Lojas consultadas (nome; endereço, telefone e e-mail **opcionais**) |
| `cotacao_cotacaoitem` | Itens a cotar (insumo, avulso ou serviço) |
| `cotacao_cotacaopreco` | Oferta de UMA loja para UM item (marca, unidade, quantidade, preços, atacado, observação, `escolhido`) |

Colunas completas e restrições na seção *Banco de dados* abaixo.

## Campos de cada oferta (loja × item)

Nome da loja, item, marca, unidade de medida, quantidade, **preço unitário**, **preço no atacado**, seletor **"o preço informado é de atacado"**, **quantidade mínima para atacado** e **observação do item** (até **255 caracteres**, validado no servidor e com `maxlength` na interface). Observações gerais da cotação: até 1000.

Interpretação dos campos (decisão registrada): preços são em **centavos por unidade de medida** (R$ por kg, por litro...); *marca* é obrigatória **exceto para itens marcados como serviço**; o *preço no atacado* é opcional mas, se informado, exige a *quantidade mínima* (e não pode ser maior que o unitário); quando o seletor *"o preço informado é de atacado"* está marcado, o preço unitário **já é** o de atacado (o campo *preço no atacado* deve ficar vazio) e a quantidade mínima, se informada, passa a ser uma exigência da oferta.

## Qual preço vale (`services.avaliar_preco`)

1. Seletor *"informado é de atacado"* marcado → vale o preço unitário (atacado). **Atende** se não há mínimo ou a quantidade da oferta o alcança; abaixo do mínimo a oferta **não atende** (fica fora da disputa e não pode ser escolhida, com o motivo na tela).
2. Senão, se há preço de atacado + mínimo e a quantidade da oferta **alcança o mínimo** → vale o **atacado**.
3. Senão → vale o **preço unitário** (varejo); a tela avisa "atacado a partir de X".

## Comparação, moeda e arredondamento

- Moeda: **BRL em centavos inteiros**.
- **Total da linha** = quantidade × preço da unidade, arredondado **uma vez**, meio para cima (ex.: 0,5 × 5 centavos = 2,5 → 3).
- **Comparação:** todos os preços são normalizados para **1 KG / 1 L / 1 UN...** da dimensão do item (uma loja em gramas e outra em quilos são comparáveis). Vence o **menor preço normalizado** entre as ofertas que atendem (cálculo em Decimal exato, sem arredondar antes; exibido ao centavo). Empate → menor total → a primeira cadastrada. A oferta de menor preço é marcada "Menor preço"; o **vencedor** é escolhido manualmente (um por item) e pode ser diferente.
- A unidade da oferta deve ter a **mesma dimensão** da unidade do item.
- Alterações de preço depois da escolha **não** trocam o vencedor sozinhas; o total e a comparação são recalculados na hora.

## Fluxo e status

`rascunho` → `enviada` → `fechada` (exige ao menos um vencedor) e `cancelada`. Só rascunho/enviada aceitam alterações (itens, lojas, preços, vencedores, edição da cotação). Fechada pode ser **reaberta** (volta a *enviada*) e cancelada/excluída **somente se nenhuma conta a pagar foi gerada**. Cada mudança é auditada.

## Conta a pagar

Com a cotação **fechada**, *Gerar contas a pagar* cria **uma conta por loja vencedora** (total dos itens vencedores dela), na categoria de **despesa** e vencimento informados. Reaproveita o formulário de contas (categoria da unidade/tipo, limites) e a auditoria do financeiro; a loja guarda o vínculo (`conta_pagar`), então repetir a ação **não duplica**. Exige `financeiro.add_contapagar` e o módulo *Contas a pagar* habilitado, além de alterar a cotação.

## Exportação / impressão

`cotacao/<id>/exportar?formato=pdf|xlsx` (e `GET /api/v1/cotacoes/{id}/exportar/`): uma linha por oferta (aplicado, tipo, total, preço por KG/L/UN, situação *Menor preço/VENCEDOR/Não atende*, observações), resumo por loja vencedora, total geral e observações gerais. O PDF é o formato de impressão. Só cotações do escopo do usuário (tenant); texto do usuário é escapado (PDF) e neutralizado contra fórmulas (Excel); cada exportação é auditada.

## Tenant, permissões e auditoria

- Toda rota busca a cotação no escopo da ação (`clientes_dos_itens`); outra empresa = **404**. Itens, lojas e preços são sempre buscados **dentro da cotação**; loja/item/preço de outra cotação são recusados.
- `cotacao.view/add/change/delete_cotacao` (padrões: Administrador tudo; Gestor ver/criar/editar; Operador ver/criar; Visualizador ver). Toda escrita nas sub-rotas exige `change`.
- Auditados (módulo `cotacao`): criação/edição/exclusão da cotação, itens, lojas e preços; escolha do vencedor; mudanças de status; exportações; vínculo da conta a pagar. Contato da loja e demais campos sensíveis (`endereco`, `telefone`, `email`) aparecem na trilha só como "(conteúdo omitido)". Excluir uma cotação apaga em cascata seus itens/lojas/preços (a trilha registra a cotação).

## Telas e API

Telas: `cotacao` (lista com filtros e status), `cotacao/nova` (opção de copiar a lista de compras; também `?da_lista=1` pelo botão da lista), `cotacao/<id>` (lojas, itens, comparativo por item, resumo dos vencedores e ações), formulários de loja/item/preço, `importar-lista`, `gerar-contas`.

API `/api/v1/cotacoes/` (Bearer + módulo `cotacao`): `GET/POST /`, `GET/PUT/PATCH/DELETE /{id}/` (detalhe com lojas, itens, ofertas avaliadas e vencedores); sob `/{id}/`: `itens/` e `itens/{item}/`, `lojas/` e `lojas/{loja}/`, `precos/` e `precos/{preco}/`, `importar-lista/`, `escolher/` `{"preco": id}`, `limpar-escolha/` `{"item": id}`, `status/` `{"acao": "enviar|fechar|reabrir|cancelar"}`, `gerar-contas/` `{"categoria": id, "vencimento": "AAAA-MM-DD"}`, `comparativo/`, `exportar/?formato=`. Dinheiro em centavos. Erros: 400 validação, 403 permissão, 404 fora do tenant/inexistente, 422 regra de negócio (ex.: cotação fechada, sem vencedor, conta já gerada).

## Banco de dados

Migrations `cotacao.0001_cotacoes` (tabelas novas; nada existente é alterado) e `modulo.0013_modulo_cotacao` (catálogo: módulo `cotacao`, ordem 18, nos segmentos padrão e habilitado para clientes com segmento).

**`cotacao_cotacao`**: `id`; `cliente_id` FK (PROTECT); `titulo` varchar(150); `observacoes` text (≤1000, padrão `''`); `status` varchar(10) (padrão `rascunho`); `criado_por_id` FK usuário (SET NULL, nulo); `enviada_em`/`fechada_em` datetime (nulos); `criado_em`/`atualizado_em`. Índice (`cliente_id`, `status`).

**`cotacao_cotacaoloja`**: `id`; `cotacao_id` FK (CASCADE); `nome` varchar(150); `endereco` varchar(200), `telefone` varchar(20), `email` varchar(254) (todos `''`); `conta_pagar_id` OneToOne `financeiro_contapagar` (SET NULL, nulo); datas. Único (`cotacao_id`, `nome`).

**`cotacao_cotacaoitem`**: `id`; `cotacao_id` FK (CASCADE); `insumo_id` FK (PROTECT, nulo); `descricao` varchar(150); `quantidade` decimal(15,4) > 0; `medida_id` FK (PROTECT); `servico` bool (falso); `observacao` varchar(255) (`''`); datas. Único (`cotacao_id`, `insumo_id`) quando `insumo_id` não é nulo.

**`cotacao_cotacaopreco`**: `id`; `item_id` FK (CASCADE); `loja_id` FK (CASCADE); `marca` varchar(60) (`''`); `medida_id` FK (PROTECT); `quantidade` decimal(15,4) > 0; `preco_unitario` bigint ≥ 1 (centavos); `preco_atacado` bigint ≥ 1 (nulo); `preco_e_atacado` bool (falso); `qtd_minima_atacado` decimal(15,4) (nulo); `observacao` varchar(255) (`''`); `escolhido` bool (falso); datas. Únicos: (`item_id`, `loja_id`) e (`item_id`) onde `escolhido` (**um vencedor por item** garantido no banco).

Impacto em dados existentes: nenhum.
