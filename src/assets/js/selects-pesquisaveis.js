/**
 * Campo de pesquisa em TODOS os dropdowns (<select>) do sistema, via select2.
 *
 * - Inicia sozinho em cada <select> da página e nos criados depois (linhas novas de formulário):
 *   um MutationObserver cobre o que o JavaScript das páginas acrescenta.
 * - Teclado: setas/Enter/Esc e digitação funcionam (nativo do select2). O campo de busca está
 *   sempre visível (minimumResultsForSearch: 0) e mostra "Nenhum resultado encontrado" quando vazio.
 * - Pesquisar não muda o valor selecionado: só filtra a lista; o valor muda ao escolher uma opção.
 * - Não mexe em: <select multiple> já tratado pela página, selects com data-sem-busca (ex.: itens
 *   por página), os do personalizador de tema e os internos do SweetAlert2.
 * - Eventos: o select2 dispara "change" pelo jQuery, que também aciona atributos onchange e os
 *   ouvintes das páginas; nada precisa mudar nelas.
 * - Listas longas carregadas do servidor continuam usando busca no servidor onde a página já a tem;
 *   as demais listas já vêm completas no HTML e são filtradas no navegador.
 */
(function () {
  'use strict';
  var $ = window.jQuery;
  if (!$ || !$.fn.select2) return;

  var IDIOMA = {
    noResults: function () { return 'Nenhum resultado encontrado'; },
    searching: function () { return 'Buscando...'; },
    loadingMore: function () { return 'Carregando mais resultados...'; },
    errorLoading: function () { return 'Não foi possível carregar os resultados.'; },
    inputTooShort: function (args) { return 'Digite ' + (args.minimum - args.input.length) + ' ou mais caracteres'; }
  };

  function elegivel(select) {
    return !select.hasAttribute('data-sem-busca') && !select.closest('.template-customizer, .select2-container, .swal2-container') &&
      !select.closest('template') && !$(select).data('select2') && select.name !== 'per_page';
  }

  function iniciar(select) {
    if (!elegivel(select)) return;
    var $select = $(select);
    // Largura: colunas "automáticas" e barras de filtro usam a largura atual do campo; o resto ocupa 100%.
    var emLinha = select.closest('.col-auto, .col-md-auto, .d-flex, .input-group, .btn-group') !== null;
    $select.select2({
      width: emLinha ? 'element' : '100%',
      language: IDIOMA,
      minimumResultsForSearch: 0,
      dropdownParent: $(select.closest('.modal') || document.body),
      placeholder: select.getAttribute('data-placeholder') || undefined,
      allowClear: false
    });
  }

  function varrer(raiz) {
    (raiz.querySelectorAll ? raiz.querySelectorAll('select') : []).forEach(iniciar);
    if (raiz.matches && raiz.matches('select')) iniciar(raiz);
  }

  function ligar() {
    varrer(document);
    new MutationObserver(function (mudancas) {
      mudancas.forEach(function (m) { m.addedNodes.forEach(function (no) { if (no.nodeType === 1) varrer(no); }); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', ligar); } else { ligar(); }
})();
