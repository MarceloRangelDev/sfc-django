/**
 * Dimensões do metro (linear, área, volume) em insumo, processado e ficha técnica.
 *
 * Só a unidade Metro (ids em data-metro-ids no <select>, marcados pelo servidor) mostra o bloco de dimensões. Com 1 dimensão
 * vale o campo "quantidade" de sempre; com 2 ou 3 o campo some e aparecem comprimento, largura (e altura): o
 * servidor calcula o produto (web_project/regras/conversao.py::quantidade_por_dimensoes) — nada é calculado aqui.
 *
 * Marcação (partials/dimensoes_campos.html): [data-dim] = medida | quantidade | dimensoes | bloco | campo-comprimento
 * | campo-largura | campo-altura. O contêiner é a linha da tabela (<tr>) ou, no insumo, o formulário.
 */
(function () {
  'use strict';

  function conteiner(el) { return el.closest('tr') || el.closest('form'); }
  function achar(c, nome) { return c.querySelector('[data-dim="' + nome + '"]'); }

  function atualizar(c) {
    var medida = achar(c, 'medida'), bloco = achar(c, 'bloco');
    if (!medida || !bloco) return;
    var metro = (medida.dataset.metroIds || '').split(',').indexOf(medida.value) !== -1;
    var dimensoes = achar(c, 'dimensoes');
    var n = metro ? parseInt(dimensoes.value, 10) || 1 : 1;
    // fora do metro vale 1 dimensão (o servidor também ignora o resto)
    if (!metro) dimensoes.value = '1';
    bloco.classList.toggle('d-none', !metro);
    var quantidade = achar(c, 'quantidade');
    if (quantidade) quantidade.classList.toggle('d-none', n > 1);
    [['comprimento', 1], ['largura', 2], ['altura', 3]].forEach(function (par) {
      var coluna = c.querySelector('[data-dim="col-' + par[0] + '"]');
      if (coluna) coluna.classList.toggle('d-none', n < par[1]);
    });
  }

  function aoMudar(e) {
    var el = e.target;
    if (el.matches && el.matches('[data-dim="medida"], [data-dim="dimensoes"]')) atualizar(conteiner(el));
  }

  // Delegado no documento: vale também para linhas adicionadas depois (modelo do formset). O jQuery cobre o 'change' do select2.
  document.addEventListener('change', aoMudar);
  if (window.jQuery) window.jQuery(document).on('change', '[data-dim="medida"], [data-dim="dimensoes"]', aoMudar);

  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType === 1 && n.querySelector && n.querySelector('[data-dim="medida"]')) atualizar(conteiner(n.querySelector('[data-dim="medida"]')));
      });
    });
  }).observe(document.body, { childList: true, subtree: true });

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-dim="medida"]').forEach(function (el) { atualizar(conteiner(el)); });
  });
})();
