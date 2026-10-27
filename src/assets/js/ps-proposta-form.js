/**
 * Formulário de proposta de serviço: linhas de trabalho e de custos + prévia do cálculo.
 *
 * NENHUM cálculo é feito aqui: a cada alteração o navegador envia o formulário ao servidor (POST em data-calculo-url) e só EXIBE o que ele
 * devolve (centavos formatados) — o mesmo cálculo que grava a proposta. As linhas viajam como JSON nos campos ocultos `itens_json` e
 * `custos_json`; o servidor valida tudo de novo ao salvar.
 *
 * Marcação: <form id="formProposta" data-calculo-url data-modos data-categorias data-repasses>, tabelas #tabelaItens e #tabelaCustos,
 * botões #adicionarItem e #adicionarCusto, painel #resultadoProposta.
 */
(function () {
  'use strict';
  var form = document.getElementById('formProposta');
  if (!form) return;
  var modos = JSON.parse(form.dataset.modos), categorias = JSON.parse(form.dataset.categorias), repasses = JSON.parse(form.dataset.repasses);
  var tItens = document.querySelector('#tabelaItens tbody'), tCustos = document.querySelector('#tabelaCustos tbody');
  var campoItens = document.getElementById('itens_json'), campoCustos = document.getElementById('custos_json');
  var temporizador = null, ultima = 0;

  function el(tag, attrs, filhos) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    (filhos || []).forEach(function (f) { e.appendChild(f); });
    return e;
  }
  function celula(conteudo) { return el('td', {}, [conteudo]); }
  function entrada(nome, valor, extra) {
    var i = el('input', Object.assign({ type: 'text', class: 'form-control form-control-sm', 'data-campo': nome }, extra || {}));
    i.value = valor == null ? '' : valor;
    return i;
  }
  function lista(nome, opcoes, valor) {
    // data-sem-busca: sem o select2 global, que dentro de célula de tabela fica sem largura e some; min-width garante a coluna visível.
    var s = el('select', { class: 'form-select form-select-sm', 'data-campo': nome, 'data-sem-busca': '', style: 'min-width:150px' });
    opcoes.forEach(function (o) {
      var op = el('option', { value: o[0] }); op.textContent = o[1]; if (o[0] === valor) op.selected = true; s.appendChild(op);
    });
    return s;
  }
  function remover(corpo) {
    var b = el('button', { type: 'button', class: 'btn btn-sm btn-text-danger', 'aria-label': 'Remover' });
    b.innerHTML = '<i class="icon-base ti tabler-trash"></i>';
    b.addEventListener('click', function (e) { e.target.closest('tr').remove(); agendar(); });
    return b;
  }

  function linhaItem(d) {
    d = d || {};
    var tr = el('tr', {}, [
      celula(entrada('descricao', d.descricao, { maxlength: 150, placeholder: 'Ex.: instalação' })),
      celula(lista('modo', modos, d.modo || form.elements.modo.value || 'hora')),
      celula(entrada('quantidade', d.quantidade || '1', { inputmode: 'decimal', style: 'min-width:80px' })),
      celula(entrada('unidade', d.unidade, { maxlength: 20, style: 'min-width:80px' })),
      celula(entrada('horas_por_unidade', d.horas_por_unidade, { inputmode: 'decimal', placeholder: 'padrão', style: 'min-width:80px' })),
      celula(entrada('preco_unitario', d.preco_unitario, { inputmode: 'decimal', placeholder: 'opcional', style: 'min-width:100px' })),
      celula(remover())
    ]);
    tItens.appendChild(tr);
  }
  function linhaCusto(d) {
    d = d || {};
    tCustos.appendChild(el('tr', {}, [
      celula(lista('categoria', categorias, d.categoria || 'material')),
      celula(entrada('descricao', d.descricao, { maxlength: 150 })),
      celula(entrada('quantidade', d.quantidade || '1', { inputmode: 'decimal', style: 'min-width:80px' })),
      celula(entrada('unidade', d.unidade, { maxlength: 20, style: 'min-width:80px' })),
      celula(entrada('custo_unitario', d.custo_unitario, { inputmode: 'decimal', style: 'min-width:100px' })),
      celula(lista('repasse', repasses, d.repasse || 'incluido')),
      celula(remover())
    ]));
  }

  function ler(corpo) {
    return Array.prototype.map.call(corpo.querySelectorAll('tr'), function (tr) {
      var o = {};
      tr.querySelectorAll('[data-campo]').forEach(function (c) { o[c.dataset.campo] = c.value; });
      return o;
    });
  }
  function serializar() {
    campoItens.value = JSON.stringify(ler(tItens));
    campoCustos.value = JSON.stringify(ler(tCustos));
  }

  function moeda(c) { return 'R$ ' + (c / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function texto(id, valor) { var e = document.getElementById(id); if (e) e.textContent = valor; }

  function exibir(r, custoHora) {
    texto('rCusto', moeda(r.custo_total)); texto('rMinimo', moeda(r.preco_minimo)); texto('rAlvo', moeda(r.preco_alvo));
    texto('rTotal', moeda(r.total)); texto('rImpostos', moeda(r.impostos_taxas)); texto('rLucro', moeda(r.lucro));
    texto('rMargem', r.margem == null ? '—' : r.margem.replace('.', ',') + '%');
    texto('rMarkup', r.markup_resultante == null ? '—' : r.markup_resultante.replace('.', ',') + '%');
    texto('rCustoHora', moeda(Math.round(parseFloat(custoHora))));
    var origem = { preco_fechado: 'preço fechado informado', precos_dos_itens: 'quantidade × preço unitário dos itens', cobranca_minima: 'cobrança mínima', preco_alvo: 'preço-alvo (custo ÷ (1 − impostos − taxas − margem))' };
    texto('rOrigem', 'Preço obtido por: ' + (origem[r.origem_preco] || r.origem_preco) + (r.metodo_preco === 'markup' ? ' (markup)' : ''));
    var caixa = document.getElementById('rAlertas'); caixa.textContent = '';
    r.alertas.forEach(function (a) {
      var d = el('div', { class: 'alert alert-' + (a.nivel === 'erro' ? 'danger' : 'warning') + ' py-2 mb-2', role: 'alert' }); d.textContent = a.mensagem; caixa.appendChild(d);
    });
  }
  function erro(msgs) {
    var caixa = document.getElementById('rAlertas'); caixa.textContent = '';
    msgs.forEach(function (m) { var d = el('div', { class: 'alert alert-danger py-2 mb-2', role: 'alert' }); d.textContent = m; caixa.appendChild(d); });
  }

  function atualizar() {
    serializar();
    var id = ++ultima;
    var dados = new FormData(form);
    dados.set('proposta', form.dataset.proposta || '');
    fetch(form.dataset.calculoUrl, { method: 'POST', body: dados, headers: { 'Accept': 'application/json', 'X-CSRFToken': form.querySelector('input[name=csrfmiddlewaretoken]').value } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (id !== ultima) return;  // resposta antiga: descarta
        if (x.ok && x.j.ok) exibir(x.j.resultado, x.j.custo_hora);
        else erro([].concat.apply([], Object.keys(x.j.erros || {}).map(function (k) { return x.j.erros[k]; })));
      })
      .catch(function () { /* mantém os valores atuais; o servidor recalcula ao salvar */ });
  }
  function agendar() { clearTimeout(temporizador); temporizador = setTimeout(atualizar, 400); }

  form.addEventListener('input', agendar);
  form.addEventListener('change', agendar);
  form.addEventListener('submit', serializar);
  document.getElementById('adicionarItem').addEventListener('click', function () { linhaItem(); });
  document.getElementById('adicionarCusto').addEventListener('click', function () { linhaCusto(); });

  JSON.parse(form.dataset.itens || '[]').forEach(linhaItem);
  JSON.parse(form.dataset.custos || '[]').forEach(linhaCusto);
  if (!tItens.children.length) linhaItem();
  atualizar();
})();
