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
      { element: '#tabelaItens', title: 'Ficha técnica', text: 'Os insumos usados, a unidade e a quantidade. O custo de cada item e o total aparecem na hora, calculados pelo sistema com a conversão das unidades. Dá para usar outro processado como insumo. Com a unidade Metro, também dá para usar área ou volume (comprimento × largura × altura).', position: 'top' },
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
      { element: '#tabelaItens', title: 'Itens', text: 'Escolha o insumo na lista (dá para buscar digitando). A unidade já vem na maior grandeza (Kg, L, Un) e o custo de cada item aparece na linha. Com a unidade Metro, escolha linear, área ou volume e informe comprimento, largura e altura; o item e o insumo precisam ter o mesmo número de dimensões.', position: 'top' },
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
      { element: '#tabelaItens', title: 'Itens da ficha', text: 'Os insumos da ficha e, quando houver, a mão de obra, cada um com seu custo. O rodapé mostra o subtotal dos insumos, o da mão de obra e o custo total da ficha (que soma os dois).', position: 'left' },
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
    'estoque-produtos': [
      { element: '.py-4 a.btn-primary', title: 'Nova movimentação', text: 'Lance entradas, baixas manuais (com motivo) e ajustes do saldo de um produto.', position: 'bottom' },
      { element: '.nav-pills', title: 'Abas', text: 'Saldos de insumos, movimentações, saldos dos produtos, transferências e alertas.', position: 'bottom' },
      FILTROS('Pesquise por nome, filtre por zerados e, na matriz, por unidade.'),
      TABELA('Saldo de cada produto que controla estoque. A venda no PDV baixa o saldo sozinha; produtos entram aqui ao ativar “Controlar estoque” no cadastro.')
    ],
    'estoque-produto-movimentar': [
      { element: '#id_produto', title: 'Produto', text: 'Só aparecem produtos ativos da sua unidade que controlam estoque.', position: 'bottom' },
      { element: '#id_tipo', title: 'Tipo', text: 'Entrada, saída (baixa manual) ou ajuste de inventário.', position: 'bottom' },
      { element: '#id_quantidade', title: 'Quantidade', text: 'Quantidade do movimento. Uma baixa nunca deixa o saldo negativo.', position: 'bottom' },
      { element: '[data-so-saida]', title: 'Motivo da baixa', text: 'Obrigatório nas baixas: perda, vencimento, quebra, consumo interno, acerto de inventário ou outro (descreva na observação).', position: 'top' },
      SALVAR
    ],
    'estoque-produto-movimentos': [
      FILTROS('Filtre por texto, tipo, período e unidade.'),
      TABELA('Histórico imutável: entradas, baixas manuais, baixas e estornos de venda do PDV, com o saldo após cada movimento. Para corrigir, registre um ajuste.')
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
    'ps-propostas': [
      NOVO('Nova proposta', 'Monte uma proposta de serviço: o sistema calcula custo, preço mínimo, preço-alvo, lucro e margem.'),
      FILTROS('Pesquise por número, cliente ou serviço e filtre pela situação da proposta.'),
      TABELA('Total e lucro estimado de cada proposta. Lucro em vermelho indica prejuízo. Abra uma proposta para aprovar, imprimir e acompanhar a execução.')
    ],
    'ps-proposta-form': [
      { element: '#ps-itens', title: 'Trabalho', text: 'Escolha a forma de cobrança (hora, dia, semana, quinzena, mês, unidade ou pacote), a quantidade e, se quiser, o preço unitário.', position: 'top' },
      { element: '#ps-custos', title: 'Custos diretos', text: 'Materiais, deslocamento, terceiros e outros. Defina se são discriminados ao cliente, incluídos no preço ou absorvidos por você.', position: 'top' },
      { element: '#ps-preco', title: 'Formação do preço', text: 'Margem é percentual do preço de venda; markup é percentual do custo. Aqui também ficam preço fechado, mínimo, desconto e acréscimos.', position: 'top' },
      { element: '#resultadoProposta', title: 'Resumo calculado', text: 'Custo, preço mínimo, preço-alvo, impostos e taxas estimados, lucro e margem. Os alertas avisam se o preço fica abaixo do custo ou da margem desejada.', position: 'left' },
      SALVAR
    ],
    'ps-proposta-detalhe': [
      { element: '#ps-acoes', title: 'Ações', text: 'Edite, imprima, envie, aprove ou cancele. O aceite registra quem aprovou e quando.', position: 'bottom' },
      { element: '#ps-resultado', title: 'Resultado estimado', text: 'Como o preço foi obtido: custo, preço mínimo, preço-alvo, impostos e taxas, lucro e margem.', position: 'left' },
      { element: '#ps-premissas', title: 'Premissas da proposta', text: 'O custo-hora e os percentuais ficam gravados na proposta. Mudar a configuração depois não altera propostas antigas.', position: 'left' },
      { element: '#ps-previsto-realizado', title: 'Previsto x realizado', text: 'Depois de aprovada, registre horas, custos, extras e pagamentos reais e compare com a estimativa.', position: 'left' }
    ],
    'ps-config': [
      { element: '#id_remuneracao_mensal', title: 'Remuneração desejada', text: 'Quanto você quer ganhar pelo seu trabalho. Entra no custo-hora e não é lucro.', position: 'bottom' },
      { element: '#id_horas_nao_faturaveis', title: 'Horas não faturáveis', text: 'Tempo gasto com orçamentos, compras, atendimento e cobrança. As horas faturáveis são as disponíveis menos estas.', position: 'bottom' },
      { element: '#id_margem_pct', title: 'Margem desejada', text: 'Percentual do preço de venda que você quer como lucro. Impostos, taxas e margem juntos precisam ficar abaixo de 100%.', position: 'bottom' },
      { element: '#ps-custo-hora', title: 'Custo-hora calculado', text: 'Veja a composição: remuneração, despesas e provisões divididas pelas horas faturáveis.', position: 'left' },
      SALVAR
    ],
    'ps-despesas': [
      NOVO('Nova despesa', 'Cadastre despesas fixas e indiretas (aluguel, internet, contabilidade...) e provisões mensais.'),
      FILTROS('Pesquise pela descrição e filtre por status.'),
      TABELA('Cada valor é convertido para base mensal conforme a periodicidade e entra no custo-hora.')
    ],
    'ps-despesa-form': [
      { element: '#id_valor', title: 'Valor', text: 'Valor da despesa na periodicidade escolhida.', position: 'bottom' },
      { element: '#id_periodicidade', title: 'Periodicidade', text: 'Semanal, mensal, anual etc. O sistema converte para base mensal.', position: 'bottom' },
      SALVAR
    ],
    'ps-modelos': [
      NOVO('Novo modelo', 'Salve serviços que você repete, com forma de cobrança, preço-base e margem própria.'),
      FILTROS('Pesquise pelo nome ou categoria e filtre por status.'),
      TABELA('Use o ícone de nova proposta para iniciar uma proposta com os valores do modelo. Mudar o modelo depois não altera propostas já feitas.')
    ],
    'ps-modelo-form': [
      { element: '#id_modo', title: 'Forma de cobrança', text: 'Hora, dia, semana, quinzena, mês, unidade ou pacote.', position: 'bottom' },
      { element: '#id_margem_pct', title: 'Margem própria', text: 'Opcional. Vazio usa a margem da configuração; cada serviço pode ter a sua.', position: 'bottom' },
      SALVAR
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
    'cozinha': [
      { element: '[data-tour="coluna-novos"]', title: 'Pedidos novos', text: 'Pedidos que ainda aguardam confirmação (manual ou pelo pagamento). Confirme para mandá-los à fila.', position: 'bottom' },
      { element: '#colPreparo', title: 'Em preparo', text: 'Inicie o preparo de um pedido confirmado e marque como pronto. Os atalhos de teclado estão em "Atalhos (?)".', position: 'bottom' },
      { element: '#colPronto', title: 'Prontos', text: 'Use "Finalizar" quando o pedido for entregue. O resultado aparece num aviso; finalizar não tem como desfazer.', position: 'left' },
      { element: '#cozinhaApp', title: 'Atualização automática', text: 'A tela se atualiza sozinha quando chega um pedido (por exemplo, enviado de uma comanda). Não precisa recarregar.', position: 'top' }
    ],
    'pedidos': [
      FILTROS('Filtre os pedidos por status, tipo e período.'),
      TABELA('Cada pedido com mesa ou cliente, status e a venda gerada. Abra um pedido para acompanhá-lo.')
    ],
    'pdv-vendas': [
      FILTROS('Filtre as vendas por período, operador, origem e status.'),
      TABELA('Todas as vendas do PDV, com o selo "Entregue" quando todos os pedidos da cozinha foram finalizados. Abra uma venda para ver itens, pagamentos e o comprovante.')
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
    // ------------------------------------------------------------------ Telas de formulário, detalhe e apoio
    'permissoes': [
      { element: '.table-responsive', title: 'Permissões por módulo', text: 'Lista o que cada módulo permite fazer (ver, criar, alterar, excluir). Use-a como referência ao montar os papéis.', position: 'top' },
      { element: 'h6', title: 'Outras permissões', text: 'Ações específicas que não cabem na matriz, como enviar orçamento ou abrir caixa.', position: 'top' }
    ],
    'assinatura': [
      { element: '[name="plano"]', title: 'Plano', text: 'Escolha o plano da assinatura. O valor e a recorrência vêm do cadastro de planos.', position: 'bottom' },
      { element: '[name="forma"]', title: 'Forma de pagamento', text: 'Boleto ou cartão. Os dados do cartão são enviados direto ao provedor de pagamento, nunca ficam no sistema.', position: 'bottom' },
      { element: '.table-responsive', title: 'Cobranças', text: 'Histórico de cobranças da assinatura, com vencimento, valor e situação.', position: 'top' }
    ],
    'auditoria-detalhe': [
      { element: 'h4', title: 'Registro de auditoria', text: 'Quem fez a ação, quando e em qual módulo. Este registro não pode ser alterado nem apagado.', position: 'bottom' },
      { element: '.table-responsive', title: 'Alterações', text: 'Cada campo alterado, com o valor antes e depois.', position: 'top' }
    ],
    'compras-form': [
      { element: '#id_insumo', title: 'Insumo', text: 'Escolha um insumo cadastrado para a lista de compras usar a unidade e o estoque dele.', position: 'bottom' },
      { element: '#id_descricao', title: 'Descrição', text: 'Para um item que não é insumo, descreva-o aqui.', position: 'bottom' },
      { element: '#id_quantidade', title: 'Quantidade', text: 'Quanto precisa comprar, na unidade escolhida ao lado.', position: 'bottom' },
      SALVAR
    ],
    'consumidor-form': [
      { element: '#id_tipo_pessoa', title: 'Tipo de pessoa', text: 'Pessoa física ou jurídica: muda o documento pedido (CPF ou CNPJ).', position: 'bottom' },
      { element: '#id_documento', title: 'Documento', text: 'CPF ou CNPJ, validado pelos dígitos verificadores. Fica protegido: só quem tem permissão o vê completo.', position: 'bottom' },
      { element: '#id_consentimento_lgpd', title: 'Consentimento (LGPD)', text: 'Registre se o consumidor autorizou o uso dos dados para contato.', position: 'top' },
      SALVAR
    ],
    'cotacao-form': [
      { element: '#id_titulo', title: 'Título', text: 'Nome da cotação, por exemplo "Compras da semana".', position: 'bottom' },
      { element: '#importar_lista', title: 'Lista de compras', text: 'Marque para já trazer os itens da lista de compras para a cotação.', position: 'bottom' },
      SALVAR
    ],
    'cotacao-detalhe': [
      { element: 'a[href*="formato=pdf"]', title: 'Exportar', text: 'Gere o PDF ou o Excel do comparativo para imprimir ou enviar.', position: 'bottom' },
      { element: 'h5', title: 'Lojas', text: 'As lojas pesquisadas. Adicione cada loja e depois o preço de cada item nela.', position: 'bottom' },
      { element: '.table-responsive', title: 'Comparativo', text: 'Compare os preços por item, escolha o vencedor e feche a cotação para gerar as contas a pagar.', position: 'top' }
    ],
    'cotacao-filho-form': [
      { element: '.card form', title: 'Dados', text: 'Preencha os campos pedidos para a loja, o item ou o preço da cotação.', position: 'bottom' },
      SALVAR
    ],
    'cotacao-importar': [
      { element: '.card form', title: 'Itens da lista de compras', text: 'Marque os itens que quer copiar para a cotação.', position: 'bottom' },
      { element: '.card form .btn-primary', title: 'Copiar', text: 'Copia os itens marcados. Os que já estão na cotação não duplicam.', position: 'top' }
    ],
    'cotacao-contas': [
      { element: '#id_categoria', title: 'Categoria', text: 'Categoria financeira das contas a pagar que serão geradas, uma por loja vencedora.', position: 'bottom' },
      { element: '#id_vencimento', title: 'Vencimento', text: 'Data de vencimento das contas geradas.', position: 'bottom' }
    ],
    'custo-comum-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome do custo comum, como "Energia elétrica".', position: 'bottom' },
      { element: '#id_tipo', title: 'Fixo ou variável', text: 'Fixo não muda com as vendas (aluguel); variável acompanha o movimento.', position: 'bottom' },
      { element: '#id_descricao', title: 'Descrição', text: 'Ajuda quem for escolher este custo no Custo operacional.', position: 'bottom' },
      SALVAR
    ],
    'estoque-etiquetas': [
      { element: '#id_insumo', title: 'Insumo', text: 'O insumo que vai na etiqueta, com QR Code.', position: 'bottom' },
      { element: '#id_formato', title: 'Formato', text: 'Folha A4 ou impressora térmica.', position: 'bottom' },
      { element: '#id_copias', title: 'Cópias', text: 'Quantas etiquetas imprimir (1 a 200).', position: 'bottom' },
      { element: '#id_validade', title: 'Validade', text: 'Informe a validade impressa na etiqueta (ou deixe o sistema calcular pelo cadastro do insumo).', position: 'top' }
    ],
    'estoque-insumo': [
      { element: 'h4', title: 'Insumo', text: 'Saldo atual, mínimo e unidade deste insumo na sua unidade.', position: 'bottom' },
      { element: '.table-responsive', title: 'Lotes e movimentações', text: 'Cada lote com saldo e validade; abaixo, as últimas movimentações. Saídas consomem primeiro o lote que vence antes.', position: 'top' }
    ],
    'estoque-minimo': [
      { element: '#id_minimo', title: 'Estoque mínimo', text: 'Abaixo desta quantidade o sistema avisa. Zero desliga o alerta.', position: 'bottom' },
      { element: '#id_fator_reposicao', title: 'Fator de reposição', text: 'A lista de compras sugere repor até mínimo × fator (2 = o dobro do mínimo).', position: 'bottom' },
      { element: '#id_multiplo_compra', title: 'Múltiplo de compra', text: 'Opcional: arredonda a sugestão para cima ao tamanho da embalagem.', position: 'bottom' },
      SALVAR
    ],
    'estoque-movimento-editar': [
      { element: '#id_quantidade', title: 'Quantidade', text: 'Corrija a quantidade: os saldos do lote e do histórico posterior são recalculados. Não pode deixar o saldo negativo.', position: 'bottom' },
      { element: '#id_data', title: 'Data', text: 'Data e hora da movimentação (não pode ser futura).', position: 'bottom' },
      { element: '#id_observacao', title: 'Observação', text: 'Registre o motivo da correção; ela fica na auditoria.', position: 'bottom' },
      SALVAR
    ],
    'estoque-transferencia': [
      { element: 'h4', title: 'Transferência', text: 'Origem, destino, responsável e data da transferência entre unidades.', position: 'bottom' },
      { element: '.table-responsive', title: 'Itens movimentados', text: 'Cada lote que saiu da origem e entrou no destino. Para desfazer, registre uma nova transferência no sentido contrário.', position: 'top' }
    ],
    'fiscal-config': [
      { element: '#id_ambiente', title: 'Ambiente', text: 'Homologação para testes ou produção. Esta versão ainda não emite documento fiscal real.', position: 'bottom' },
      { element: '#id_provedor', title: 'Provedor', text: 'Serviço que emitirá os documentos fiscais.', position: 'bottom' },
      { element: '#id_serie', title: 'Série', text: 'Série dos documentos emitidos.', position: 'bottom' },
      { element: '#id_habilita_nfce', title: 'Tipos de documento', text: 'Habilite só os tipos que a empresa emite.', position: 'top' }
    ],
    'fiscal-documento': [
      { element: 'h4', title: 'Documento fiscal', text: 'Situação e dados do documento ligado à venda do PDV.', position: 'bottom' },
      { element: 'form[action*="cancelar"]', title: 'Cancelar documento', text: 'Informe o motivo (mínimo de 15 caracteres) para cancelar. Fica registrado na auditoria.', position: 'top' }
    ],
    'imagens-painel': [
      { element: '#mes', title: 'Mês', text: 'Escolha o mês (AAAA-MM) para ver o uso e o custo estimado do armazenamento.', position: 'bottom' },
      { element: '.table-responsive', title: 'Uso e custo', text: 'Uso, franquia gratuita e unidades cobradas de cada item. O valor é estimado, não é fatura.', position: 'top' }
    ],
    'insumo-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome do insumo. Pode vir de um insumo padrão do seu segmento.', position: 'bottom' },
      { element: '#id_medida', title: 'Unidade de medida', text: 'Unidade em que o insumo é comprado e controlado no estoque.', position: 'bottom' },
      { element: '#id_quantidade', title: 'Quantidade comprada', text: 'Quanto veio na embalagem comprada. Se a unidade for Metro, aparece a escolha de dimensões: linear (comprimento), área (comprimento × largura, em m²) ou volume (com altura, em m³). Nesses casos o sistema calcula a quantidade e o preço passa a ser por m² ou m³.', position: 'bottom' },
      { element: '#id_preco_pago', title: 'Preço pago', text: 'Quanto pagou pela quantidade acima; o custo unitário é calculado.', position: 'bottom' },
      SALVAR
    ],
    'insumo-historico': [
      { element: '#campo', title: 'Campo', text: 'Veja a evolução de um campo específico (preço, quantidade...).', position: 'bottom' },
      { element: '#de', title: 'Período', text: 'Limite o histórico por data inicial e final.', position: 'bottom' },
      { element: '.table-responsive', title: 'Linha do tempo', text: 'Cada alteração do insumo, com quem alterou e o valor antes e depois.', position: 'top' }
    ],
    'insumo-global-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome do insumo padrão que ficará disponível para os segmentos escolhidos.', position: 'bottom' },
      { element: '#id_medida', title: 'Unidade', text: 'Unidade de medida padrão.', position: 'bottom' },
      { element: '#id_marca', title: 'Marca', text: 'Marca de referência (opcional).', position: 'bottom' },
      { element: '#id_descricao', title: 'Descrição', text: 'Texto de apoio para quem for escolher este insumo.', position: 'bottom' }
    ],
    'insumo-nao-conformes': [
      FILTROS('Filtre os insumos que se afastam do padrão do segmento.'),
      TABELA('Insumos cujo nome, marca ou unidade fogem do insumo padrão. Corrija-os para manter os custos comparáveis.')
    ],
    'compromisso-form': [
      { element: '#id_tipo', title: 'Tipo', text: 'Reserva, entrega, compromisso, evento ou tarefa.', position: 'bottom' },
      { element: '#id_titulo', title: 'Título', text: 'O que aparece na agenda e no calendário conectado.', position: 'bottom' },
      { element: '#id_inicio', title: 'Início e fim', text: 'Dia e hora. Marque "Dia inteiro" para eventos sem horário.', position: 'bottom' },
      { element: '#id_lembrete_minutos', title: 'Lembrete', text: 'Aviso enviado antes do início, uma única vez.', position: 'bottom' },
      SALVAR
    ],
    'integracao-detalhe': [
      { element: 'form[action*="testar"]', title: 'Testar conexão', text: 'Confere se as credenciais ainda funcionam, sem alterar nada.', position: 'bottom' },
      { element: 'form[action*="sincronizar"]', title: 'Sincronizar', text: 'Traz e envia a agenda agora, sem esperar a sincronização automática.', position: 'bottom' },
      { element: 'form[action*="desconectar"]', title: 'Desconectar', text: 'Revoga o acesso e apaga as credenciais guardadas.', position: 'top' }
    ],
    'integracao-form': [
      { element: 'form[autocomplete="off"]', title: 'Credenciais', text: 'Informe os dados do provedor. Eles são guardados criptografados e nunca aparecem de novo na tela.', position: 'bottom' },
      { element: 'form[autocomplete="off"] button[type="submit"]', title: 'Salvar e testar', text: 'Grava as credenciais e já testa a conexão.', position: 'top' }
    ],
    'integracao-mensagens': [
      { element: '#id_contato', title: 'Contato', text: 'Número ou identificador do destinatário.', position: 'bottom' },
      { element: '#id_texto', title: 'Mensagem', text: 'Texto enviado pela conexão. Fora da janela de 24 h do WhatsApp só templates aprovados são aceitos.', position: 'bottom' },
      { element: '.table-responsive', title: 'Histórico', text: 'Mensagens enviadas e recebidas, com o estado de entrega.', position: 'top' }
    ],
    'kanban': [
      { element: '#btnColuna', title: 'Nova coluna', text: 'Crie as etapas do seu fluxo (a fazer, em andamento, concluído...).', position: 'bottom' },
      { element: '#btnCartao', title: 'Novo cartão', text: 'Crie uma tarefa; clique nela para editar ou excluir.', position: 'bottom' },
      { element: '#kanban', title: 'Quadro', text: 'Arraste os cartões entre as colunas para atualizar o andamento.', position: 'top' }
    ],
    'mao-obra-config': [
      { element: '#id_jornada_horas', title: 'Jornada', text: 'Horas de trabalho por dia, base do cálculo da hora extra.', position: 'bottom' },
      { element: '#id_horas_mes', title: 'Horas e dias por mês', text: 'Usados para transformar um valor mensal em valor por hora e por dia.', position: 'bottom' },
      { element: '#id_extra_percentual', title: 'Hora extra', text: 'Percentual adicional pago sobre a hora normal.', position: 'bottom' },
      SALVAR
    ],
    'mao-obra-relatorio': [
      FILTROS('Escolha o período e o colaborador do relatório.'),
      TABELA('Horas, dias e custo de mão de obra apurados dos apontamentos do período.')
    ],
    'medida-form': [
      { element: '.card form', title: 'Medida', text: 'Nome, sigla e dimensão (massa, volume, unidade...). A conversão entre medidas só vale dentro da mesma dimensão.', position: 'bottom' },
      { element: '.card form .btn-primary', title: 'Salvar', text: 'Grava a medida; ela passa a valer nos insumos e fichas.', position: 'top' }
    ],
    'modulo-cliente': [
      { element: '#cliente', title: 'Cliente', text: 'Escolha o cliente cujos módulos quer habilitar.', position: 'bottom' },
      { element: 'input[name="modulos"]', title: 'Módulos', text: 'Marque os módulos que o cliente pode usar. O menu dele passa a mostrar só esses.', position: 'top' },
      { element: '[data-tour="ordem-menu"]', title: 'Ordem no menu', text: 'Arraste os módulos (ou use as setas) para escolher a ordem do menu dos usuários deste cliente. A ordem é salva na hora; "Restaurar" volta ao padrão.', position: 'top' }
    ],
    'modulo-form': [
      { element: '.card form', title: 'Dados do módulo', text: 'Nome, identificador, rota e posição no menu do módulo.', position: 'bottom' },
      { element: '#instrucoes-de-uso', title: 'Instruções de uso', text: 'Como usar o módulo, para consulta rápida.', position: 'top' }
    ],
    'modulo-lista': [
      NOVO('Novo módulo', 'Cadastre um módulo no catálogo do sistema.'),
      TABELA('Os módulos disponíveis, com a ordem no menu, a rota e o status. Abra um módulo para editar e ver as instruções de uso.')
    ],
    'segmento-form': [
      { element: '#id_nome', title: 'Nome', text: 'Nome do segmento de trabalho (por exemplo, Padaria).', position: 'bottom' },
      { element: '#id_tipo_precificacao', title: 'Precificação', text: 'Define se o segmento precifica produtos, serviços ou ambos.', position: 'bottom' },
      { element: '#filtroInsumosGlobais', title: 'Insumos padrão', text: 'Escolha quais insumos padrão ficam disponíveis para os clientes do segmento.', position: 'top' }
    ],
    'segmento-lista': [
      NOVO('Novo segmento', 'Cadastre um segmento de trabalho e escolha os módulos que ele libera.'),
      TABELA('Cada segmento, seu tipo de precificação e os módulos habilitados.')
    ],
    'notificacao-form': [
      { element: '#id_tipo_destinatario', title: 'Destinatários', text: 'Todos os usuários, um segmento ou usuários específicos.', position: 'bottom' },
      { element: '#id_titulo', title: 'Título', text: 'Texto curto que aparece na notificação.', position: 'bottom' },
      { element: '#id_mensagem', title: 'Mensagem', text: 'O conteúdo completo.', position: 'bottom' },
      { element: '#id_link', title: 'Link', text: 'Opcional: página do sistema aberta ao clicar.', position: 'top' }
    ],
    'notificacao-minhas': [
      { element: '.nav-pills', title: 'Filtro', text: 'Veja todas, só as não lidas ou só as lidas.', position: 'bottom' },
      { element: '.card', title: 'Notificações', text: 'Clique numa notificação para abri-la e marcá-la como lida. As lidas são removidas depois de 48 horas.', position: 'top' }
    ],
    'pdv-caixas': [
      TABELA('Caixas abertos e fechados, com operador, horários e diferença na conferência.'),
      { element: '.table-responsive tbody tr:first-child a', title: 'Detalhe', text: 'Abra o caixa para conferir o dinheiro, as sangrias e as vendas.', position: 'left' }
    ],
    'pdv-caixa': [
      { element: 'form[action*="sangria"]', title: 'Sangria', text: 'Retire dinheiro do caixa informando o motivo. Entra na conferência.', position: 'bottom' },
      { element: 'form[action*="suprimento"]', title: 'Suprimento', text: 'Coloque dinheiro no caixa (troco, por exemplo).', position: 'bottom' },
      { element: 'form[action*="fechar"]', title: 'Fechar caixa', text: 'Informe o dinheiro contado; a diferença para o esperado fica registrada.', position: 'top' },
      { element: '[data-tour="cracha"]', title: 'Código de autorização', text: 'Quem autoriza descontos, cancelamentos e limpeza gera aqui o código do crachá (barras/QR) para autorizar no PDV sem digitar a senha.', position: 'top' }
    ],
    'pdv-caixa-detalhe': [
      { element: 'form[action*="conferir"]', title: 'Conferência', text: 'Quem gere o caixa confere o fechamento e registra uma observação.', position: 'bottom' },
      TABELA('Sangrias, suprimentos e vendas do caixa.')
    ],
    'pdv-comandas': [
      { element: 'form[action*="abrir"]', title: 'Abrir comanda', text: 'Escolha a mesa e abra a comanda; os itens são lançados e pagos depois.', position: 'bottom' },
      TABELA('Comandas abertas, com mesa, itens e total. Abra uma para lançar itens ou receber.')
    ],
    'pdv-comanda': [
      { element: '#formLeitor', title: 'Leitor', text: 'Leia o código de barras ou QR do produto para lançá-lo na comanda.', position: 'bottom' },
      { element: '[data-tour="estoque-produto"]', title: 'Estoque', text: 'Cada produto mostra o saldo em estoque ou "Estoque não controlado".', position: 'top' },
      { element: '#pdvComanda', title: 'Itens', text: 'Itens lançados e total, calculados pelo servidor. Cada item mostra se é Novo ou já foi Enviado à cozinha.', position: 'top' },
      { element: '#pdvDesconto', title: 'Desconto', text: 'Qualquer desconto exige a senha ou o código do crachá de quem tem permissão; o servidor confere e registra quem autorizou.', position: 'left' },
      { element: '#pdvAddPagamento', title: 'Pagamento', text: 'Uma ou mais formas de pagamento (conta dividida).', position: 'left' },
      { element: '#pdvFinalizar', title: 'Finalizar', text: 'Fecha a comanda, baixa o estoque e gera o recebimento.', position: 'left' }
    ],
    'pdv-venda-detalhe': [
      { element: 'a[href*="comprovante"]', title: 'Comprovante', text: 'Comprovante não fiscal em tela, PDF ou arquivo para impressora térmica.', position: 'bottom' },
      { element: '[data-bs-toggle="collapse"]', title: 'Devolução', text: 'Devolva itens de uma venda paga; o estoque só é reposto se o item não foi preparado.', position: 'left' },
      { element: 'form[action*="cancelar"]', title: 'Cancelar venda', text: 'Informe o motivo e a autorização (senha ou crachá) de quem pode cancelar. Venda paga só pode ser cancelada com o caixa ainda aberto.', position: 'top' }
    ],
    'forma-pagamento-form': [
      { element: '#id_nome', title: 'Nome', text: 'Como a forma aparece no PDV.', position: 'bottom' },
      { element: '#id_tipo', title: 'Tipo', text: 'Dinheiro, cartão, Pix... O dinheiro entra na conferência do caixa.', position: 'bottom' },
      { element: '#id_taxa_percentual', title: 'Taxa', text: 'Percentual e/ou valor fixo descontado do que a empresa recebe.', position: 'bottom' },
      { element: '#forma-global', title: 'Forma global', text: 'Só o administrador: libera esta forma para os segmentos escolhidos.', position: 'top' },
      SALVAR
    ],
    'mesa-form': [
      { element: '#id_nome', title: 'Nome da mesa', text: 'Como a mesa aparece ao abrir a comanda (ex.: Mesa 4).', position: 'bottom' },
      SALVAR
    ],
    'pdv-config': [
      { element: '#id_controla_estoque', title: 'Controle de estoque', text: 'Quando ligado, a venda baixa o estoque dos insumos da ficha e dos produtos que controlam estoque.', position: 'bottom' },
      { element: '#id_confirmacao_pedido', title: 'Confirmação de pedido', text: 'Define se o pedido precisa ser confirmado antes de ir para a cozinha.', position: 'bottom' },
      { element: '#id_formato_comprovante', title: 'Comprovante', text: 'Formato e rodapé do comprovante não fiscal.', position: 'bottom' },
      SALVAR
    ],
    'pedido-detalhe': [
      { element: 'h5', title: 'Pedido', text: 'Número, situação e itens. Confirme, avance as etapas da cozinha ou volte uma etapa.', position: 'bottom' },
      { element: 'input[name="motivo"]', title: 'Cancelar pedido', text: 'O motivo é obrigatório para cancelar.', position: 'top' },
      { element: '.card-header h6', title: 'Histórico', text: 'Cada mudança de situação, com quem fez e quando.', position: 'top' }
    ],
    'usuario-form': [
      { element: '#formUsuario', title: 'Dados do usuário', text: 'Nome, e-mail, papel e unidade. O papel define o que a pessoa vê e faz.', position: 'bottom' },
      { element: '#verPermissoesEspeciais', title: 'Permissões especiais', text: 'Libere ou bloqueie ações específicas só para este usuário, além do papel.', position: 'top' },
      { element: '#formUsuario button.btn-primary[type="submit"]', title: 'Salvar', text: 'Grava o usuário. Novas senhas são definidas pelo próprio usuário por e-mail.', position: 'top' }
    ],
    'pdv-venda': [
      { element: '#pdvBusca', title: 'Buscar produto', text: 'Digite o nome ou a categoria, ou leia o código de barras/QR. Catálogos grandes mostram um aviso: use a busca para achar o resto.', position: 'bottom' },
      { element: '#pdvProdutos', title: 'Produtos do PDV', text: 'Só aparecem produtos com precificação marcada para o PDV, ativos e com preço. Na lista de Precificação, a coluna PDV explica quando algum não aparece.', position: 'top' },
      { element: '#pdvCarrinho', title: 'Venda', text: 'Itens, quantidades e totais calculados pelo servidor. Cada produto da lista mostra o estoque disponível.', position: 'left' },
      { element: '#pdvDesconto', title: 'Desconto', text: 'Descontos exigem a senha ou o código do crachá de quem tem permissão; a autorização é pedida ao finalizar.', position: 'left' },
      { element: '#pdvLimpar', title: 'Limpar', text: 'Descarta a venda em andamento. Mostra o que será apagado e exige a autorização de quem pode.', position: 'top' },
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
