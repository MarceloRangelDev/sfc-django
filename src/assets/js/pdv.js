/**
 * Tela do PDV (balcão) e recebimento de comanda.
 *
 * IMPORTANTE: tudo que aparece aqui (preços, totais, troco) é só PRÉVIA. O servidor recalcula preço e ficha a partir do
 * cadastro, valida os pagamentos e devolve o resultado oficial; o navegador nunca decide valor.
 * Cada tentativa de finalizar leva uma `chave` única: se a rede cair e a tela reenviar, o servidor devolve a MESMA venda.
 */
(function () {
  'use strict';

  function centavos(texto) {
    var d = String(texto || '').replace(/\D/g, '');
    return d ? parseInt(d, 10) : 0;
  }
  function moeda(c) {
    var s = String(Math.abs(c)).padStart(3, '0');
    var inteiro = s.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return (c < 0 ? '-' : '') + 'R$ ' + inteiro + ',' + s.slice(-2);
  }
  function novaChave() {
    return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : 'k' + Date.now() + Math.random().toString(16).slice(2);
  }
  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value;
  }
  function el(tag, attrs, filhos) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    (filhos || []).forEach(function (f) { e.appendChild(f); });
    return e;
  }

  // ---------------- pagamentos (compartilhado entre balcão e comanda) ----------------
  function Pagamentos(raiz, formas, aoMudar) {
    var linhas = [];
    var tabela = raiz;

    function pagos() { return linhas.reduce(function (s, l) { return s + l.valor; }, 0); }
    function adicionar(valor) {
      linhas.push({ forma: formas.length ? formas[0].id : null, valor: valor || 0, recebido: 0, pagador: '' });
      desenhar();
    }
    function formaDe(id) { return formas.filter(function (f) { return f.id === id; })[0]; }

    function desenhar() {
      tabela.innerHTML = '';
      linhas.forEach(function (l, i) {
        var forma = formaDe(l.forma) || {};
        var select = el('select', { 'class': 'form-select form-select-sm', 'aria-label': 'Forma de pagamento' });
        formas.forEach(function (f) { var o = el('option', { value: f.id, text: f.nome }); if (f.id === l.forma) o.selected = true; select.appendChild(o); });
        select.addEventListener('change', function () { l.forma = parseInt(select.value, 10); if (!formaDe(l.forma) || formaDe(l.forma).tipo !== 'dinheiro') l.recebido = 0; desenhar(); aoMudar(); });
        var valor = el('input', { type: 'text', 'class': 'form-control form-control-sm text-end', inputmode: 'numeric', value: l.valor ? moeda(l.valor).replace('R$ ', '') : '', placeholder: '0,00', 'aria-label': 'Valor' });
        valor.addEventListener('input', function () { l.valor = centavos(valor.value); valor.value = l.valor ? moeda(l.valor).replace('R$ ', '') : ''; aoMudar(); });
        var linha = el('div', { 'class': 'd-flex gap-2 mb-2 align-items-center flex-wrap' }, [select, valor]);
        if (forma.tipo === 'dinheiro') {
          var rec = el('input', { type: 'text', 'class': 'form-control form-control-sm text-end', inputmode: 'numeric', placeholder: 'Recebido', value: l.recebido ? moeda(l.recebido).replace('R$ ', '') : '', 'aria-label': 'Valor recebido' });
          rec.addEventListener('input', function () { l.recebido = centavos(rec.value); rec.value = l.recebido ? moeda(l.recebido).replace('R$ ', '') : ''; aoMudar(); });
          linha.appendChild(rec);
        }
        var pagador = el('input', { type: 'text', 'class': 'form-control form-control-sm', placeholder: 'Pagador (opcional)', maxlength: 60, value: l.pagador, 'aria-label': 'Pagador' });
        pagador.addEventListener('input', function () { l.pagador = pagador.value; });
        linha.appendChild(pagador);
        var rm = el('button', { type: 'button', 'class': 'btn btn-sm btn-text-danger', 'aria-label': 'Remover pagamento', text: '✕' });
        rm.addEventListener('click', function () { linhas.splice(i, 1); desenhar(); aoMudar(); });
        linha.appendChild(rm);
        tabela.appendChild(linha);
      });
    }
    return {
      adicionar: adicionar,
      pagos: pagos,
      limpar: function () { linhas = []; desenhar(); },
      dividir: function (total, partes) {
        var base = Math.floor(total / partes), resto = total % partes;
        linhas = [];
        for (var i = 0; i < partes; i++) linhas.push({ forma: formas.length ? formas[0].id : null, valor: base + (i < resto ? 1 : 0), recebido: 0, pagador: 'Pessoa ' + (i + 1) });
        desenhar(); aoMudar();
      },
      troco: function () {
        return linhas.reduce(function (s, l) { var f = formaDe(l.forma) || {}; return s + (f.tipo === 'dinheiro' && l.recebido > l.valor ? l.recebido - l.valor : 0); }, 0);
      },
      dados: function () {
        return linhas.map(function (l) {
          var item = { forma: l.forma, valor: l.valor };
          if (l.recebido) item.valor_recebido = l.recebido;
          if (l.pagador) item.pagador = l.pagador;
          return item;
        });
      },
      preencher: function (total) { if (!linhas.length) adicionar(total); else if (linhas.length === 1) { linhas[0].valor = total; desenhar(); } },
    };
  }

  function enviar(url, corpo) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'Accept': 'application/json' }, body: JSON.stringify(corpo), credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, dados: d }; }); });
  }

  // ---------------- balcão ----------------
  function iniciarBalcao(app) {
    var formas = JSON.parse(document.getElementById('formasPdv').textContent);
    var carrinho = [], chave = novaChave(), enviando = false, descontoCentavos = 0;
    var $ = function (id) { return document.getElementById(id); };

    function subtotal() { return carrinho.reduce(function (s, i) { return s + Math.round(i.preco * i.qtd); }, 0); }
    function total() { return Math.max(0, subtotal() - descontoCentavos); }

    var pag = Pagamentos($('pdvPagamentos'), formas, atualizarTotais);

    function atualizarTotais() {
      $('pdvSubtotal').textContent = moeda(subtotal());
      $('pdvTotal').textContent = moeda(total());
      var falta = total() - pag.pagos();
      $('pdvFalta').textContent = falta > 0 ? 'Falta ' + moeda(falta) : (falta < 0 ? 'Pagamentos excedem ' + moeda(-falta) : 'Pago');
      $('pdvFalta').className = 'fw-medium ' + (falta === 0 ? 'text-success' : 'text-warning');
      $('pdvTroco').textContent = pag.troco() ? 'Troco ' + moeda(pag.troco()) : '';
      $('pdvFinalizar').disabled = enviando || !carrinho.length || falta !== 0;
    }

    function desenharCarrinho() {
      var corpo = $('pdvCarrinho');
      corpo.innerHTML = '';
      carrinho.forEach(function (i, idx) {
        var qtd = el('input', { type: 'text', 'class': 'form-control form-control-sm text-end', style: 'width:5rem', value: String(i.qtd).replace('.', ','), inputmode: 'decimal', 'aria-label': 'Quantidade de ' + i.nome });
        qtd.addEventListener('change', function () { var v = parseFloat(qtd.value.replace(',', '.')); if (v > 0) { i.qtd = v; } desenharCarrinho(); });
        var rm = el('button', { type: 'button', 'class': 'btn btn-sm btn-text-danger', 'aria-label': 'Remover ' + i.nome, text: '✕' });
        rm.addEventListener('click', function () { carrinho.splice(idx, 1); desenharCarrinho(); });
        corpo.appendChild(el('tr', {}, [
          el('td', { text: i.nome }), el('td', {}, [qtd]), el('td', { 'class': 'text-end', text: moeda(i.preco) }),
          el('td', { 'class': 'text-end', text: moeda(Math.round(i.preco * i.qtd)) }), el('td', {}, [rm]),
        ]));
      });
      pag.preencher(total());
      atualizarTotais();
    }

    function adicionar(p) {
      var existente = carrinho.filter(function (i) { return i.produto === p.produto; })[0];
      if (existente) existente.qtd += 1; else carrinho.push({ produto: p.produto, nome: p.nome, preco: p.preco, qtd: 1 });
      desenharCarrinho();
    }

    var temporizador = null;
    function buscar() {
      fetch(app.dataset.urlProdutos + '?q=' + encodeURIComponent($('pdvBusca').value), { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var lista = $('pdvProdutos');
          lista.innerHTML = '';
          (d.itens || []).forEach(function (p) {
            var b = el('button', { type: 'button', 'class': 'btn btn-outline-primary text-start', 'aria-label': 'Adicionar ' + p.nome }, [
              el('div', { 'class': 'fw-medium', text: p.nome }), el('small', { 'class': 'text-muted', text: p.categoria + ' · ' + p.preco_formatado }),
            ]);
            b.addEventListener('click', function () { adicionar(p); });
            lista.appendChild(el('div', { 'class': 'col-6 col-lg-4' }, [b]));
          });
          if (!d.itens || !d.itens.length) lista.appendChild(el('div', { 'class': 'col-12 text-muted', text: 'Nenhum produto com preço do PDV encontrado.' }));
        });
    }
    $('pdvBusca').addEventListener('input', function () { clearTimeout(temporizador); temporizador = setTimeout(buscar, 250); });
    $('pdvAddPagamento').addEventListener('click', function () { pag.adicionar(Math.max(0, total() - pag.pagos())); });
    document.querySelectorAll('[data-dividir]').forEach(function (b) { b.addEventListener('click', function () { pag.dividir(total(), parseInt(b.dataset.dividir, 10)); }); });
    if ($('pdvDesconto')) {
      $('pdvDesconto').addEventListener('input', function () { descontoCentavos = centavos(this.value); this.value = descontoCentavos ? moeda(descontoCentavos).replace('R$ ', '') : ''; pag.limpar(); pag.preencher(total()); atualizarTotais(); });
    }
    $('pdvLimpar').addEventListener('click', function () { carrinho = []; descontoCentavos = 0; if ($('pdvDesconto')) $('pdvDesconto').value = ''; pag.limpar(); chave = novaChave(); desenharCarrinho(); $('pdvMsg').textContent = ''; });

    $('pdvFinalizar').addEventListener('click', function () {
      enviando = true; atualizarTotais();
      $('pdvMsg').className = 'alert alert-info mt-3'; $('pdvMsg').textContent = 'Registrando a venda...';
      enviar(app.dataset.urlFinalizar, {
        itens: carrinho.map(function (i) { return { produto: i.produto, quantidade: i.qtd }; }), pagamentos: pag.dados(), desconto: descontoCentavos,
        observacao: $('pdvObs').value, consumidor: $('pdvConsumidor') ? $('pdvConsumidor').value : '', chave: chave,
      }).then(function (r) {
        enviando = false;
        if (!r.ok) { $('pdvMsg').className = 'alert alert-danger mt-3'; $('pdvMsg').textContent = r.dados.erro || 'Não foi possível registrar a venda.'; atualizarTotais(); return; }
        $('pdvMsg').className = 'alert alert-success mt-3';
        $('pdvMsg').innerHTML = '';
        $('pdvMsg').appendChild(document.createTextNode('Venda #' + r.dados.numero + ' registrada: ' + r.dados.total_formatado + (r.dados.troco ? ' · troco ' + r.dados.troco_formatado : '') + '. '));
        var a = el('a', { href: r.dados.comprovante, target: '_blank', rel: 'noopener', text: 'Abrir comprovante' });
        $('pdvMsg').appendChild(a);
        carrinho = []; descontoCentavos = 0; if ($('pdvDesconto')) $('pdvDesconto').value = ''; pag.limpar(); chave = novaChave(); desenharCarrinho();
      }).catch(function () {
        // Falha de rede: a chave é mantida. Tocar em Finalizar de novo reenvia com segurança (sem duplicar a venda).
        enviando = false; $('pdvMsg').className = 'alert alert-warning mt-3'; $('pdvMsg').textContent = 'Sem resposta do servidor. Toque em Finalizar novamente: a venda não será duplicada.'; atualizarTotais();
      });
    });

    buscar(); desenharCarrinho();
  }

  // ---------------- comanda ----------------
  function iniciarComanda(app) {
    var formas = JSON.parse(document.getElementById('formasPdv').textContent);
    var totalBase = parseInt(app.dataset.total, 10), desconto = 0, chave = novaChave(), enviando = false;
    var $ = function (id) { return document.getElementById(id); };
    var pag = Pagamentos($('pdvPagamentos'), formas, atualizar);
    function total() { return Math.max(0, totalBase - desconto); }
    function atualizar() {
      $('pdvTotal').textContent = moeda(total());
      var falta = total() - pag.pagos();
      $('pdvFalta').textContent = falta > 0 ? 'Falta ' + moeda(falta) : (falta < 0 ? 'Pagamentos excedem ' + moeda(-falta) : 'Pago');
      $('pdvTroco').textContent = pag.troco() ? 'Troco ' + moeda(pag.troco()) : '';
      $('pdvFinalizar').disabled = enviando || falta !== 0 || total() <= 0;
    }
    $('pdvAddPagamento').addEventListener('click', function () { pag.adicionar(Math.max(0, total() - pag.pagos())); });
    document.querySelectorAll('[data-dividir]').forEach(function (b) { b.addEventListener('click', function () { pag.dividir(total(), parseInt(b.dataset.dividir, 10)); }); });
    if ($('pdvDesconto')) $('pdvDesconto').addEventListener('input', function () { desconto = centavos(this.value); this.value = desconto ? moeda(desconto).replace('R$ ', '') : ''; pag.limpar(); pag.preencher(total()); atualizar(); });
    $('pdvFinalizar').addEventListener('click', function () {
      enviando = true; atualizar();
      enviar(app.dataset.urlPagar, { pagamentos: pag.dados(), desconto: desconto, chave: chave }).then(function (r) {
        enviando = false;
        if (!r.ok) { $('pdvMsg').className = 'alert alert-danger mt-3'; $('pdvMsg').textContent = r.dados.erro || 'Não foi possível receber.'; atualizar(); return; }
        window.location.href = r.dados.comprovante;
      }).catch(function () { enviando = false; $('pdvMsg').className = 'alert alert-warning mt-3'; $('pdvMsg').textContent = 'Sem resposta do servidor. Toque em Receber novamente: não será cobrado duas vezes.'; atualizar(); });
    });
    pag.preencher(total()); atualizar();
  }

  document.addEventListener('DOMContentLoaded', function () {
    var balcao = document.getElementById('pdvApp'), comanda = document.getElementById('pdvComanda');
    if (balcao) iniciarBalcao(balcao);
    if (comanda) iniciarComanda(comanda);
  });
})();
