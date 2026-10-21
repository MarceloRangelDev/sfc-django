/**
 * Tours das telas dos módulos de negócio (Processados, Canais, Produtos, Fichas,
 * Precificação, Custo operacional, Estoque, Financeiro e Painel), num lugar só.
 *
 * Cada tela chama, no bloco page_js do seu template:
 *
 *     <script>initTourModulo('processados');</script>
 *
 * e este arquivo (carregado em templates/layout/partials/scripts.html, depois do
 * tour.js) monta os passos. O algoritmo, o botão "?" e o "Não mostrar novamente"
 * (por usuário) são os do tour.js (ver apps/tour/README.md). Passos cujo elemento
 * não existe na página — por exemplo um botão que o usuário não tem permissão de
 * ver — são ignorados automaticamente.
 *
 * Para criar o tour de uma tela nova: acrescente uma chave em TOURS (a chave é a
 * mesma de initTourModulo e vira a preferência "não mostrar novamente"; use só
 * letras minúsculas, números e hífen) e chame initTourModulo na tela. `element` é
 * qualquer seletor CSS; prefira ids/atributos estáveis.
 */
(function () {
  'use strict';

  // Blocos que se repetem nas listagens.
  var NOVO = function (titulo, texto) { return { element: '.py-4 a.btn-primary', title: titulo, text: texto, position: 'bottom' }; };
  var FILTROS = function (texto) { return { element: '.card form[method="get"]', title: 'Filtros', text: texto, position: 'bottom' }; };
  var EXPORTAR = { element: 'a[href*="export=xlsx"]', title: 'Exportar', text: 'Baixe a lista em Excel ou PDF. O arquivo respeita os filtros aplicados e só traz o que você pode ver.', position: 'bottom' };
  var TABELA = function (texto) { return { element: '.table-responsive', title: 'Tabela', text: texto, position: 'top' }; };
  var SALVAR = { element: '[data-botao-salvar]', title: 'Salvar', text: 'Use "Salvar" para voltar à lista ou "Salvar e adicionar outro" para cadastrar em sequência.', position: 'top' };
  var UNIDADE = { element: '[name="unidade"]', title: 'Unidade (matriz e filiais)', text: 'Se você puder ver as filiais, escolha aqui de quem quer ver os dados. O selo Matriz/Filial na tabela mostra a origem e, ao passar o mouse, qual unidade é.', position: 'bottom' };

  var TOURS = {
    // ------------------------------------------------------------------ Processados
    'processados': [
      NOVO('Novo processado', 'Cadastre uma receita ou insumo processado: ficha técnica com os insumos usados e o rendimento.'),
      FILTROS('Pesquise pelo nome, veja ativos/inativos e, se tiver acesso às filiais, filtre por unidade.'),
      TABELA('Custo total, custo por kg/L/un e o fator de rendimento (quanto sobra depois do preparo). Cada processado vira um insumo de 1 kg, 1 L ou 1 un, que você usa em outras receitas e fichas.')
    ],
    'processado-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome da receita/processado. Ele também será o nome do insumo gerado.', position: 'bottom' },
      { element: '#id_rendimento_quantidade', title: 'Rendimento', text: 'Quanto a receita rende depois de pronta (ex.: 500 g de camarão limpo). Abaixo do campo aparece o valor por extenso, como "500 gramas".', position: 'bottom' },
      { element: '#id_rendimento_medida', title: 'Unidade de saída', text: 'Gramas, quilos, mililitros, litros, unidade ou metro. O insumo gerado sempre usa a maior grandeza: 1 kg, 1 L, 1 un ou 1 m.', position: 'bottom' },
      { element: '#tabelaItens', title: 'Ficha técnica', text: 'Os insumos usados, a unidade e a quantidade. O custo de cada item e o total aparecem na hora, calculados pelo sistema com a conversão das unidades. Dá para usar outro processado como insumo.', position: 'top' },
      { element: '#adicionarLinha', title: 'Adicionar insumo', text: 'Inclua quantos insumos precisar.', position: 'top' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Canais de venda
    'canais-venda': [
      NOVO('Novo canal', 'Cadastre onde você vende (iFood, balcão, WhatsApp...) e as taxas de cada canal.'),
      FILTROS('Pesquise pelo nome ou descrição e filtre por status.'),
      TABELA('Total de taxas (percentuais e em reais), o líquido que sobra depois delas e a descrição de cada taxa. A Precificação usa esses valores.')
    ],
    'canal-form': [
      { element: '#id_nome', title: 'Nome do canal', text: 'O nome exibido, por exemplo IFOOD.', position: 'bottom' },
      { element: '#id_descricao', title: 'Descrição', text: 'Distingue variações do mesmo canal, como "Motoboy plataforma - com antecipação".', position: 'bottom' },
      { element: '#tabelaTaxas', title: 'Taxas', text: 'Cada linha é uma taxa em porcentagem OU em reais. A soma das porcentagens deve ficar abaixo de 100%.', position: 'top' },
      { element: '#adicionarLinha', title: 'Adicionar taxa', text: 'Inclua uma linha para cada taxa do canal (comissão, antecipação, embalagem...).', position: 'top' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Categorias
    'categorias': [
      NOVO('Nova categoria', 'Crie categorias para organizar os produtos.'),
      FILTROS('Pesquise pelo nome e filtre por ativas/inativas.'),
      TABELA('Quantidade vendida, faturamento, ticket médio e % do faturamento de cada categoria. Use as ações para editar, ativar/desativar ou excluir (categoria em uso é só desativada).')
    ],
    'categoria-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome da categoria.', position: 'bottom' },
      { element: '#id_quantidade_vendida', title: 'Quantidade vendida', text: 'Informe a quantidade vendida da categoria no período (use o mesmo período dos custos operacionais).', position: 'bottom' },
      { element: '#id_faturamento', title: 'Faturamento', text: 'Faturamento da categoria no período.', position: 'bottom' },
      { element: '#previaTicket', title: 'Ticket médio', text: 'Calculado enquanto você preenche: faturamento ÷ quantidade vendida. Sem vendas, fica R$ 0,00.', position: 'top' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Produtos
    'produtos': [
      NOVO('Novo produto', 'Cadastre os produtos que você vende, cada um em uma categoria.'),
      FILTROS('Pesquise, filtre por categoria, status e unidade.'),
      EXPORTAR,
      TABELA('Ordene pelos títulos das colunas. O custo do produto vem das fichas técnicas e o preço, das precificações.')
    ],
    'produto-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome do produto.', position: 'bottom' },
      { element: '#id_categoria', title: 'Categoria', text: 'Só aparecem as categorias ativas da sua unidade.', position: 'bottom' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Fichas técnicas
    'fichas': [
      NOVO('Nova ficha', 'Monte a ficha técnica de um produto: os insumos e as quantidades usadas.'),
      FILTROS('Pesquise por ficha ou produto, filtre por status e unidade.'),
      EXPORTAR,
      TABELA('O custo total é recalculado sozinho quando o preço de um insumo ou de um processado muda.')
    ],
    'ficha-form': [
      { element: '#id_produto', title: 'Produto', text: 'A ficha pertence a um produto e não tem nome próprio: ela usa o nome do produto. Um produto pode ter mais de uma ficha.', position: 'bottom' },
      { element: '.total-ficha', title: 'Custo total', text: 'Aparece aqui em cima e também no rodapé da tabela, sempre com o mesmo valor, atualizado enquanto você preenche.', position: 'bottom' },
      { element: '#tabelaItens', title: 'Itens', text: 'Escolha o insumo na lista (dá para buscar digitando). A unidade já vem na maior grandeza (Kg, L, Un) e o custo de cada item aparece na linha.', position: 'top' },
      { element: '#adicionarLinha', title: 'Adicionar insumo', text: 'Inclua mais linhas quando precisar.', position: 'top' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Precificação
    'precificacoes': [
      NOVO('Nova precificação', 'Calcule o preço de venda de uma ficha técnica para um canal de venda. Pode haver várias precificações para o mesmo produto.'),
      FILTROS('Filtre por produto, canal, método do lucro, status e unidade.'),
      EXPORTAR,
      { element: '[data-tour="visao"]', title: 'Tabela ou cartões', text: 'Escolha como ver as precificações: em tabela ou em cartões (um por lançamento). O sistema lembra a sua escolha.', position: 'bottom' },
      TABELA('Custo total, lucro, preço de venda e CMV de cada precificação. Tudo é recalculado quando o custo da ficha ou as taxas do canal mudam.')
    ],
    'precificacao-form': [
      { element: '#id_ficha + .select2-container', title: 'Ficha técnica', text: 'Escolha a ficha (produto) a precificar. Não é preciso dar nome: a precificação fica ligada à ficha e ao canal.', position: 'bottom' },
      { element: '#id_canal', title: 'Canal de venda', text: 'Use um canal cadastrado ou deixe "Personalizado" para informar nome, descrição e taxas em % e/ou em reais.', position: 'bottom' },
      { element: '#id_custo_operacional', title: 'Custo operacional', text: 'Já vem preenchido pela distribuição do Custo operacional da categoria do produto. Se você digitar outro valor, ele fica marcado como "Ajustado manualmente" e não é sobrescrito; "Restaurar automático" volta ao calculado.', position: 'bottom' },
      { element: '#id_metodo_lucro', title: 'Como formar o lucro', text: 'Margem (% sobre o preço de venda), markup (% sobre o custo) ou um valor em reais.', position: 'bottom' },
      { element: '#tabelaItens', title: 'Itens da ficha', text: 'Os itens da ficha com seus custos e o custo total no topo e no rodapé.', position: 'left' },
      { element: '#resumo', title: 'Resumo do cálculo', text: 'Canal, custos, taxas, lucro, preço de venda e CMV. É calculado pelo servidor e atualizado enquanto você preenche.', position: 'left' },
      SALVAR
    ],

    // ------------------------------------------------------------------ Custo operacional
    'custos': [
      { element: '.py-4 a.btn-outline-secondary', title: 'Por categoria', text: 'Veja quanto do custo operacional cada categoria recebe, em R$, e ajuste manualmente se precisar.', position: 'bottom' },
      NOVO('Novo custo', 'Cadastre um custo fixo (aluguel, internet) ou variável (água, energia).'),
      { element: 'a[href*="?modelo="]', title: 'Custos comuns', text: 'Atalhos para custos comuns (água, energia, aluguel...). Nada é criado até você adicionar e informar o seu valor.', position: 'bottom' },
      FILTROS('Filtre por tipo (fixo/variável), status e unidade.'),
      TABELA('Valor mensal de cada custo. A distribuição entre as categorias é calculada sozinha (proporcional às vendas) na tela "por categoria".')
    ],
    'custo-form': [
      { element: '#id_modelo', title: 'Custo comum', text: 'Opcional: escolha um custo do catálogo para já vir com o tipo e o nome.', position: 'bottom' },
      { element: '#id_valor', title: 'Valor mensal', text: 'O valor é sempre o seu, do mês.', position: 'bottom' },
      { element: '.alert-info', title: 'Distribuição automática', text: 'Você informa só o valor do custo. O rateio por categoria, em R$, é calculado pelas vendas e pode ser ajustado em "Custo operacional por categoria".', position: 'top' },
      SALVAR
    ],
    'custo-categorias': [
      { element: '[data-tour="criterio"]', title: 'Como é calculado', text: 'O custo operacional total é dividido entre as categorias na proporção das vendas. Ex.: R$ 300 com 3 categorias de 100 vendas cada = R$ 100 por categoria.', position: 'bottom' },
      { element: '.table-responsive', title: 'Custo por categoria', text: 'Valor em R$ de cada categoria e custo por unidade. Use Ajustar para fixar um valor e Restaurar para voltar ao cálculo automático. A Precificação usa esse valor.', position: 'top' }
    ],
    'custos-comuns': [
      NOVO('Novo custo comum', 'Cadastre custos comuns da sua empresa (água, energia, aluguel...) para usá-los como atalho no Custo operacional.'),
      FILTROS('Pesquise e filtre por tipo, origem (padrão do sistema ou próprio) e status.'),
      TABELA('Os padrões do sistema são somente leitura; os seus podem ser editados. Um custo já usado é apenas desativado ao excluir.')
    ],

    // ------------------------------------------------------------------ Estoque
    'estoque': [
      { element: '.py-4 a.btn-primary', title: 'Nova movimentação', text: 'Registre entradas, saídas e ajustes de inventário.', position: 'bottom' },
      { element: '.py-4 a.btn-outline-primary', title: 'Transferir', text: 'Mova estoque entre a matriz e as filiais (só para usuários autorizados).', position: 'bottom' },
      { element: '.nav-pills', title: 'Abas', text: 'Saldos, histórico de movimentações, transferências e alertas.', position: 'bottom' },
      { element: '.alert-warning', title: 'Alertas', text: 'Itens abaixo do mínimo e lotes vencidos ou perto de vencer.', position: 'bottom' },
      FILTROS('Pesquise e filtre por situação (abaixo do mínimo, zerado, vencendo) e unidade.'),
      EXPORTAR,
      TABELA('Saldo e mínimo de cada insumo, com o próximo vencimento. Matriz e filiais têm saldos separados. Abra o insumo para ver os lotes e definir o mínimo.')
    ],
    'estoque-movimentar': [
      { element: '#id_insumo + .select2-container', title: 'Insumo', text: 'Escolha o insumo (dá para buscar digitando).', position: 'bottom' },
      { element: '#id_tipo', title: 'Tipo', text: 'Entrada (compra), saída (consumo/perda) ou ajuste de inventário. Saídas consomem primeiro o lote que vence antes.', position: 'bottom' },
      { element: '#id_quantidade', title: 'Quantidade', text: 'Pode ser em outra unidade da mesma dimensão (ex.: 500 g para um insumo em kg).', position: 'bottom' },
      { element: '[data-so-entrada]', title: 'Lote e validade', text: 'Nas entradas, o lote é opcional e a validade pode ser informada mesmo sem lote, para acompanhar vencimentos.', position: 'top' },
      SALVAR
    ],
    'estoque-movimentos': [
      FILTROS('Filtre por texto, tipo, período e unidade.'),
      EXPORTAR,
      TABELA('Use o lápis para corrigir uma movimentação (quantidade, data, observação e validade): os saldos são recalculados e a mudança fica na auditoria. Movimentações não podem ser apagadas.')
    ],
    'estoque-transferencias': [
      { element: '.py-4 a.btn-primary', title: 'Nova transferência', text: 'Envie insumos da sua unidade para outra unidade da mesma estrutura.', position: 'bottom' },
      { element: '.table-responsive', title: 'Transferências', text: 'Origem, destino, responsável e data. Abra uma transferência para ver os lotes movimentados.', position: 'top' }
    ],
    'estoque-transferencia-form': [
      { element: '#id_destino', title: 'Destino', text: 'Outra unidade (matriz ou filial) da sua estrutura. A origem é sempre a sua unidade.', position: 'bottom' },
      { element: '#tabelaItens', title: 'Itens', text: 'O destino precisa ter o insumo cadastrado com o mesmo nome. Lote e validade acompanham a mercadoria.', position: 'top' },
      SALVAR
    ],
    'estoque-alertas': [
      { element: '#cartaoBaixo', title: 'Estoque baixo', text: 'Insumos com saldo abaixo do mínimo definido.', position: 'bottom' },
      { element: '#cartaoVencimento', title: 'Vencimentos', text: 'Lotes vencidos ou que vencem em poucos dias.', position: 'top' }
    ],

    // ------------------------------------------------------------------ Financeiro
    'contas-pagar': [
      NOVO('Nova conta', 'Lance uma conta a pagar com fornecedor, categoria, valor e vencimento.'),
      { element: '.alert-warning', title: 'Avisos', text: 'Contas atrasadas e vencendo nos próximos dias.', position: 'bottom' },
      FILTROS('Filtre por texto, categoria, situação, período de vencimento e unidade.'),
      EXPORTAR,
      TABELA('Valor, pago e saldo de cada conta. Abra a conta para pagar (total ou parcial) e ver o histórico de baixas.'),
      { element: 'tfoot', title: 'Totais', text: 'Soma do que está filtrado.', position: 'top' }
    ],
    'contas-receber': [
      NOVO('Nova conta', 'Lance uma conta a receber com cliente, categoria, valor e vencimento.'),
      { element: '.alert-warning', title: 'Avisos', text: 'Contas atrasadas e vencendo nos próximos dias.', position: 'bottom' },
      FILTROS('Filtre por texto, categoria, situação, período de vencimento e unidade.'),
      EXPORTAR,
      TABELA('Valor, recebido e saldo de cada conta. Abra a conta para receber (total ou parcial) e ver o histórico de baixas.'),
      { element: 'tfoot', title: 'Totais', text: 'Soma do que está filtrado.', position: 'top' }
    ],
    'conta-form': [
      { element: '#id_descricao', title: 'Descrição', text: 'O que é a conta (ex.: aluguel de outubro).', position: 'bottom' },
      { element: '#id_categoria', title: 'Categoria', text: 'Agrupa a conta no DRE. Só aparecem as categorias do tipo certo e ativas.', position: 'bottom' },
      { element: '#id_valor', title: 'Valor', text: 'Valor total da conta.', position: 'bottom' },
      { element: '#id_vencimento', title: 'Vencimento', text: 'Contas atrasadas e vencendo geram avisos e notificações.', position: 'bottom' },
      SALVAR
    ],
    'conta-detalhe': [
      { element: '#resumoConta', title: 'Resumo', text: 'Valor, quanto já foi baixado, saldo e situação.', position: 'bottom' },
      { element: 'form[action$="/baixar"]', title: 'Baixa', text: 'Registre o pagamento/recebimento, total ou parcial. A data não pode ser futura.', position: 'top' },
      { element: '#historicoBaixas', title: 'Histórico de baixas', text: 'As baixas não podem ser editadas: para corrigir, estorne (o estorno fica registrado).', position: 'top' }
    ],
    'financeiro-dre': [
      { element: '.alert-info', title: 'Relatório gerencial', text: 'Não é uma demonstração contábil oficial.', position: 'bottom' },
      { element: '.card form[method="get"]', title: 'Período e regime', text: 'Caixa: o que foi efetivamente recebido e pago. Vencimento: contas que vencem no período, pagas ou não.', position: 'bottom' },
      { element: '#quadroDre', title: 'DRE', text: 'Receitas, despesas por categoria e o resultado do período.', position: 'top' }
    ],
    'financeiro-balanco': [
      { element: '.alert-info', title: 'Relatório gerencial', text: 'Quadro simplificado, sem saldo inicial, não contábil.', position: 'bottom' },
      { element: '.card form[method="get"]', title: 'Posição em', text: 'Escolha a data. Cada unidade é mostrada separadamente.', position: 'bottom' },
      { element: '#quadroBalanco', title: 'Ativo e passivo', text: 'Caixa gerencial, contas a receber e estoque a custo no ativo; contas a pagar no passivo.', position: 'top' }
    ],
    'financeiro-categorias': [
      { element: '#formCategoriaFinanceira', title: 'Nova categoria', text: 'Crie categorias de receita e despesa: elas agrupam o DRE.', position: 'bottom' },
      { element: 'form[action$="/padrao"]', title: 'Categorias padrão', text: 'Cria as categorias comuns que ainda não existem, só quando você pedir.', position: 'bottom' },
      { element: '.table-responsive', title: 'Suas categorias', text: 'Ative/desative ou exclua. Categoria em uso por contas é só desativada.', position: 'top' }
    ],

    // ------------------------------------------------------------------ Painel
    'painel': [
      { element: '#filtroPainel', title: 'Período e unidade', text: 'O período vale para o resultado e os vencimentos; saldos e estoque mostram a posição de hoje. A matriz pode escolher uma filial.', position: 'bottom' },
      { element: '#btnOnline', title: 'Usuários online', text: 'Quem teve atividade nos últimos minutos, só entre os usuários que você pode visualizar.', position: 'bottom' },
      { element: 'h5.mb-3', title: 'Indicadores', text: 'Os blocos mostrados dependem das suas permissões e dos módulos habilitados para o seu cliente.', position: 'bottom' }
    ],

    // ------------------------------------------------------------------ Telas de apoio e administração
    'compras': [
      NOVO('Novo item', 'Acrescente à lista de compras um insumo ou item avulso que precisa comprar.'),
      FILTROS('Pesquise itens da lista pelo nome.'),
      TABELA('Os itens a comprar, com unidade, quantidade e origem (digitado por você ou sugerido pelo estoque).')
    ],
    'consumidores': [
      NOVO('Novo consumidor', 'Cadastre clientes finais. Dados pessoais só são guardados com o consentimento registrado.'),
      FILTROS('Pesquise por nome ou documento e filtre por status.'),
      TABELA('Contato, cidade e se há consentimento para uso dos dados. Os dados aparecem só para quem tem permissão.')
    ],
    'cotacoes': [
      NOVO('Nova cotação', 'Compare preços de itens entre lojas. Também dá para criar a partir da lista de compras.'),
      FILTROS('Pesquise pelo título e filtre por status.'),
      TABELA('Cada cotação mostra quantos itens e lojas tem e em que etapa está.')
    ],
    'medidas': [
      NOVO('Nova medida', 'Cadastre unidades de medida (ex.: caixa = 12 unidades) para usar nos insumos.'),
      FILTROS('Pesquise pelo nome ou sigla e filtre por status.'),
      TABELA('A dimensão (massa, volume, unidade...) e quanto vale na unidade-base. As conversões usam estes valores.')
    ],
    'colaboradores': [
      NOVO('Novo colaborador', 'Cadastre quem trabalha com você e o valor da hora, dia ou mês para ratear o custo de mão de obra.'),
      FILTROS('Pesquise pelo nome e filtre por unidade ou status.'),
      TABELA('Os valores por hora, dia, semana e quinzena são calculados a partir do que foi informado e da jornada.')
    ],
    'apontamentos': [
      NOVO('Novo apontamento', 'Registre as horas ou diárias trabalhadas por um colaborador em um serviço.'),
      FILTROS('Filtre por período, colaborador e unidade.'),
      TABELA('Horas trabalhadas, extras e o custo total de cada apontamento.')
    ],
    'usuarios': [
      NOVO('Novo usuário', 'Crie o acesso de uma pessoa e escolha o perfil dela. Usuários não são excluídos, só desativados.'),
      FILTROS('Pesquise por nome, usuário ou e-mail e filtre por perfil e status.'),
      TABELA('O perfil define o que cada pessoa pode ver e fazer. Você só vê os usuários da sua estrutura.')
    ],
    'mesas': [
      NOVO('Nova mesa', 'Cadastre as mesas ou comandas usadas no atendimento do PDV.'),
      FILTROS('Pesquise pelo nome e filtre por status.'),
      TABELA('Mesas inativas deixam de aparecer no PDV, mas o histórico das vendas é preservado.')
    ],
    'formas-pagamento': [
      NOVO('Nova forma', 'Cadastre como o cliente paga no PDV (dinheiro, cartão, Pix...) e a taxa de cada uma.'),
      FILTROS('Pesquise pelo nome e filtre por status.'),
      TABELA('Tipo e taxa de cada forma. Formas inativas não aparecem no PDV.')
    ],
    'insumos-globais': [
      NOVO('Novo insumo padrão', 'Cadastre o nome padrão de um insumo para que as unidades usem a mesma identificação.'),
      FILTROS('Pesquise pelo nome e filtre por segmento ou status.'),
      TABELA('A unidade sugerida, os segmentos atendidos e em quantas unidades o insumo está em uso.')
    ],
    'notificacoes-historico': [
      NOVO('Nova notificação', 'Envie um aviso para todos, para um segmento ou para usuários específicos. Você confirma antes de enviar.'),
      FILTROS('Filtre o histórico por usuário, unidade, entrega, leitura e período.'),
      TABELA('Uma linha por destinatário, com o resultado da entrega e se já leu. Notificações lidas somem depois de 48 horas.')
    ],
    'pedidos': [
      FILTROS('Filtre os pedidos por status, tipo e período.'),
      TABELA('Cada pedido com mesa ou cliente, status e a venda gerada. Abra um pedido para acompanhá-lo.')
    ],
    'pdv-vendas': [
      FILTROS('Filtre as vendas por período, operador, origem e status.'),
      TABELA('Todas as vendas do PDV. Abra uma venda para ver itens, pagamentos e o comprovante.')
    ],
    'auditoria': [
      FILTROS('Filtre o registro por usuário, módulo, ação, resultado e período.'),
      TABELA('Quem fez o quê e quando. Os registros não podem ser editados nem apagados.')
    ],
    'fiscal-documentos': [
      FILTROS('Filtre os documentos por status, ambiente e período.'),
      TABELA('Cada documento fiscal com o status devolvido pelo emissor. Sem emissão homologada, nada é marcado como autorizado.')
    ],
    'papeis': [
      NOVO('Novo papel', 'Crie um perfil de acesso e escolha o que ele pode ver, criar, alterar e excluir em cada módulo.'),
      TABELA('Quantas permissões e quantos usuários cada papel tem. Ajustar um papel muda o acesso de todos que o usam.')
    ],
    'integracoes': [
      { element: '.alert-warning', title: 'Homologação', text: 'Os conectores seguem a documentação dos provedores, mas ainda não foram homologados com contas reais. Teste antes de usar em produção.', position: 'bottom' },
      { element: '#btn-templates-whatsapp', title: 'Templates do WhatsApp', text: 'Crie, consulte, altere e exclua os modelos de mensagem da API oficial da Meta.', position: 'bottom' },
      TABELA('Suas conexões e o estado de cada uma. Abra uma conexão para testar, trocar credenciais ou ver o registro operacional.')
    ],
    'templates-whatsapp': [
      { element: '#tpl-wa-acoes a.btn-primary', title: 'Novo template', text: 'Cria um rascunho aqui. Ele só vai para a análise da Meta quando você usar Enviar à Meta.', position: 'bottom' },
      { element: '#tpl-wa-aviso', title: 'Dois estados', text: 'Situação aqui é o que acontece no sistema. Status na Meta é o que a própria Meta informou; nada é aprovado sem o retorno dela.', position: 'bottom' },
      { element: '#tpl-wa-filtros', title: 'Filtrar e sincronizar', text: 'Escolha uma conexão para filtrar e use Sincronizar com a Meta para trazer os status e importar templates criados no painel dela.', position: 'bottom' },
      { element: '#tpl-wa-tabela', title: 'Seus templates', text: 'Edite, envie à Meta ou exclua. Excluir um template já enviado também o remove na Meta.', position: 'top' }
    ],
    'template-whatsapp-form': [
      { element: '[name="nome"]', title: 'Nome', text: 'Só letras minúsculas, números e sublinhado. Depois de enviado à Meta, nome, idioma e categoria não mudam.', position: 'bottom' },
      { element: '[name="corpo"]', title: 'Corpo', text: 'O texto da mensagem. Use {{1}}, {{2}}... para variáveis, nunca no início nem no fim.', position: 'bottom' },
      { element: '[name="exemplos_corpo"]', title: 'Exemplos', text: 'A Meta exige um exemplo para cada variável, um por linha, na ordem.', position: 'bottom' },
      { element: '#tpl-wa-botoes', title: 'Botões', text: 'Respostas rápidas, link https ou telefone. Opcional.', position: 'top' },
      { element: 'form button[type="submit"].btn-primary', title: 'Salvar', text: 'Salva como rascunho. Para submeter à Meta use Enviar à Meta na lista.', position: 'top' }
    ],

    // ------------------------------------------------------------------ Orçamentos, serviços e telas de formulário
    'servicos': [
      NOVO('Novo serviço', 'Cadastre um serviço (é um produto do tipo serviço): ele aparece no apontamento, na ficha técnica, na precificação e nos orçamentos.'),
      FILTROS('Pesquise pelo nome, descrição ou categoria e veja ativos ou inativos.'),
      TABELA('Serviço usado em apontamento, ficha ou precificação não é apagado: só é desativado.')
    ],
    'servico-form': [
      { element: '#id_nome', title: 'Nome do serviço', text: 'Como o serviço aparece no apontamento, na ficha e no orçamento. Produtos e serviços compartilham os nomes: não pode repetir.', position: 'bottom' },
      { element: '#id_categoria', title: 'Categoria', text: 'Agrupa o serviço nos relatórios e na precificação. Só aparecem categorias ativas.', position: 'bottom' },
      { element: '#id_codigo_barras', title: 'Código', text: 'Opcional: em branco o sistema gera um código interno.', position: 'bottom' },
      SALVAR
    ],
    'orcamentos': [
      NOVO('Novo orçamento', 'Monte uma proposta com produtos, serviços ou os dois, com descontos e totais calculados pelo sistema.'),
      FILTROS('Procure pelo número ou pelo destinatário e filtre pela situação (rascunho, enviado, aprovado, recusado, cancelado).'),
      TABELA('Abra o orçamento para editar, enviar ou acompanhar. O ícone da impressora gera o PDF.')
    ],
    'orcamento-form': [
      { element: '#orcamento-destinatario', title: 'Destinatário', text: 'Escolha um cliente final do cadastro (os contatos são copiados) ou digite os dados. E-mail e WhatsApp são usados no envio.', position: 'bottom' },
      { element: '#orcamento-itens', title: 'Itens', text: 'Produtos e serviços do cadastro, ou linhas avulsas. Sem preço digitado vale o da precificação marcada para o PDV. O total vem do servidor.', position: 'top' },
      { element: '#adicionarLinhaOrcamento', title: 'Adicionar item', text: 'Inclua quantos itens precisar; cada linha aceita um desconto próprio.', position: 'top' },
      { element: '#id_desconto', title: 'Desconto geral', text: 'Desconto em reais sobre o total dos itens (além dos descontos de cada linha).', position: 'top' },
      SALVAR
    ],
    'orcamento-detalhe': [
      { element: '#orcamento-acoes', title: 'Ações', text: 'Editar (enquanto estiver em rascunho ou enviado), imprimir o PDF completo ou enviar.', position: 'bottom' },
      { element: '#btn-enviar-orcamento', title: 'Enviar / imprimir', text: 'Permite tirar itens só da versão enviada ou impressa, sem mexer no orçamento salvo.', position: 'bottom' },
      { element: '#orcamento-itens-detalhe', title: 'Itens e totais', text: 'Valores calculados pelo servidor, com descontos de cada item e o desconto geral.', position: 'top' },
      { element: '#orcamento-situacao', title: 'Situação', text: 'Marque aprovado, recusado ou cancele. Depois de fechado o orçamento não pode mais ser alterado.', position: 'top' }
    ],
    'orcamento-enviar': [
      { element: '#orcamento-versao-aviso', title: 'Só esta versão', text: 'Desmarcar um item o tira apenas do PDF ou da mensagem que você vai gerar agora.', position: 'bottom' },
      { element: '#orcamento-versao-itens', title: 'Itens da versão', text: 'O total do PDF é recalculado só com os itens marcados.', position: 'top' },
      { element: '#orcamento-persistir', title: 'Alteração permanente', text: 'Só se você marcar e confirmar os itens desmarcados também saem do orçamento salvo.', position: 'top' },
      { element: '#btn-gerar-pdf', title: 'PDF / impressão', text: 'Abre o PDF em outra aba para imprimir ou salvar.', position: 'top' }
    ],
    'agenda': [
      { element: '[data-visao="calendario"]', title: 'Calendário', text: 'Alterna entre a tabela e o calendário. No calendário, clique num dia para criar e num compromisso para editar; arraste para reagendar.', position: 'bottom' },
      NOVO('Novo compromisso', 'Crie reservas, entregas, eventos e tarefas. Eles sincronizam com o Google Agenda ou o iCloud quando houver conexão.'),
      { element: '.table-responsive, #agendaCalendario', title: 'Seus compromissos', text: 'Registros importados do calendário externo aparecem marcados. Dia inteiro mostra só a data.', position: 'top' }
    ],
    'cliente-form': [
      { element: '#id_cpf_cnpj', title: 'CPF/CNPJ', text: 'Identifica o cliente (e a empresa dona dos dados). É validado pelos dígitos verificadores.', position: 'bottom' },
      { element: '[name="endereco"]', title: 'Contato da empresa', text: 'Endereço, e-mail e WhatsApp aparecem nos documentos emitidos, como o PDF do orçamento. A logo é enviada abaixo.', position: 'bottom' },
      { element: '[name="usuario_username"]', title: 'Usuário inicial', text: 'Informe quem será o responsável: ele recebe por e-mail um link para definir a senha. Nenhuma senha trafega aqui.', position: 'top' },
      SALVAR
    ],
    'papel-form': [
      { element: '#nome', title: 'Nome do papel', text: 'Perfil de acesso que você atribui aos usuários.', position: 'bottom' },
      { element: '.table-responsive', title: 'Permissões por módulo', text: 'Marque o que o papel pode ver, criar, alterar e excluir em cada módulo.', position: 'top' },
      { element: '#mostrar_outras', title: 'Outras permissões', text: 'Ações específicas (enviar orçamento, abrir caixa...). O grupo começa recolhido, mas as marcações salvas continuam valendo.', position: 'top' }
    ],
    'apontamento-form': [
      { element: '#id_colaborador', title: 'Colaborador', text: 'Quem trabalhou. Os valores usados são os do cadastro dele, gravados no lançamento.', position: 'bottom' },
      { element: '#id_servico', title: 'Serviço', text: 'Opcional: um serviço cadastrado em Mão de obra > Serviços.', position: 'bottom' },
      { element: '#id_tipo', title: 'Por hora ou por dia', text: 'Por hora usa início, fim e pausa (com hora extra); por dia usa dias × diária.', position: 'bottom' },
      SALVAR
    ],
    'colaborador-form': [
      { element: '#id_nome', title: 'Nome', text: 'Quem presta o serviço (funcionário, autônomo, prestador).', position: 'bottom' },
      { element: '#id_valor_hora', title: 'Valor da hora', text: 'Base do custo de mão de obra nos apontamentos e nas fichas de serviço. Se informar o valor por mês, hora e dia são derivados dele.', position: 'bottom' },
      SALVAR
    ],
    'pdv-venda': [
      { element: '#pdvBusca', title: 'Buscar produto', text: 'Digite o nome ou a categoria, ou leia o código de barras/QR. Catálogos grandes mostram um aviso: use a busca para achar o resto.', position: 'bottom' },
      { element: '#pdvProdutos', title: 'Produtos do PDV', text: 'Só aparecem produtos com precificação marcada para o PDV, ativos e com preço. Na lista de Precificação, a coluna PDV explica quando algum não aparece.', position: 'top' },
      { element: '#pdvCarrinho', title: 'Venda', text: 'Itens, quantidades e totais calculados pelo servidor.', position: 'left' },
      { element: '#pdvPagamentos', title: 'Pagamento', text: 'Uma ou mais formas de pagamento; formas globais liberadas ao seu segmento também aparecem.', position: 'left' }
    ]
  };

  window.TOURS_MODULOS = TOURS;
  window.initTourModulo = function (chave) {
    var passos = TOURS[chave];
    if (!passos || !window.initTour) return;
    // Texto dos passos de lista usa o seletor da unidade só se existir; o tour.js ignora o resto.
    window.initTour({ page: chave, steps: passos.concat(chave.indexOf('-form') === -1 && chave !== 'painel' ? [UNIDADE] : []) });
  };
})();
