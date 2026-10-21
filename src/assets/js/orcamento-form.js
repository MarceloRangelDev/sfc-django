/**
 * Formulário de orçamento: adicionar/remover linhas e prévia dos totais.
 *
 * NENHUM cálculo é feito aqui: a cada alteração o navegador envia as linhas ao servidor (POST em data-calculo-url) e só exibe o que ele
 * devolve — o mesmo cálculo que grava o orçamento. Se a prévia falhar, o último valor fica na tela e o servidor recalcula ao salvar.
 *
 * Marcação: <form id="formOrcamento" data-calculo-url="...">; linhas em `tbody#tabelaOrcamento tr.linha-orcamento` com campos terminados em
 * -produto, -quantidade, -preco_unitario, -desconto e uma `td.total-linha`; `<template id="modeloLinhaOrcamento">`; botão
 * #adicionarLinhaOrcamento; total em #orcamentoTotal; campo geral `desconto`.
 */
(function () {
  'use strict';
  var form = document.getElementById('formOrcamento');
  if (!form) return;
  var corpo = form.querySelector('#tabelaOrcamento tbody');
  var total = document.getElementById('id_form-TOTAL_FORMS');
  var csrf = form.querySelector('input[name=csrfmiddlewaretoken]').value;
  var temporizador = null, ultima = 0;

  function linhas() { return Array.prototype.slice.call(corpo.querySelectorAll('tr.linha-orcamento')); }
  function campo(linha, sufixo) { return linha.querySelector('[name$="' + sufixo + '"]'); }

  function atualizar() {
    var id = ++ultima;
    var rows = linhas();
    var dados = rows.map(function (l) {
      var remover = campo(l, '-DELETE');
      if (remover && remover.checked) return { produto: '', quantidade: '', preco_unitario: '', desconto: '' };
      return { produto: campo(l, '-produto').value, quantidade: campo(l, '-quantidade').value, preco_unitario: campo(l, '-preco_unitario').value, desconto: campo(l, '-desconto').value };
    });
    fetch(form.dataset.calculoUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      body: JSON.stringify({ itens: dados, desconto: form.elements.desconto ? form.elements.desconto.value : '' })
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (d) {
        if (id !== ultima) return;  // resposta antiga: descarta
        rows.forEach(function (l, i) {
          var celula = l.querySelector('td.total-linha');
          var item = d.itens[i] || {};
          celula.textContent = item.total || '—';
          celula.title = item.erro || '';
          celula.classList.toggle('text-danger', !!item.erro);
          var preco = campo(l, '-preco_unitario');
          if (preco && !preco.value) preco.placeholder = item.preco || '';  // preço do PDV que será usado se o campo ficar vazio
        });
        document.getElementById('orcamentoTotal').textContent = d.total;
      })
      .catch(function () { /* mantém os valores atuais */ });
  }

  function agendar() { clearTimeout(temporizador); temporizador = setTimeout(atualizar, 300); }

  form.addEventListener('change', function (e) { if (e.target.closest('#tabelaOrcamento') || e.target.name === 'desconto') agendar(); });
  form.addEventListener('input', function (e) { if (e.target.closest('#tabelaOrcamento') || e.target.name === 'desconto') agendar(); });

  document.getElementById('adicionarLinhaOrcamento').addEventListener('click', function () {
    var html = document.getElementById('modeloLinhaOrcamento').innerHTML.replace(/__prefix__/g, total.value);
    corpo.insertAdjacentHTML('beforeend', html);
    total.value = parseInt(total.value, 10) + 1;
  });
  corpo.addEventListener('click', function (e) {
    var botao = e.target.closest('.remover-linha-orcamento');
    if (botao) { botao.closest('tr').remove(); agendar(); }
  });
  agendar();  // edição: mostra os totais calculados pelo servidor sem esperar uma digitação
})();
