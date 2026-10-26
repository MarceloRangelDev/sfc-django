/**
 * Tela do PDV (balcão) e recebimento de comanda.
 *
 * IMPORTANTE: tudo que aparece aqui (preços, totais, troco) é só PRÉVIA. O servidor recalcula preço e ficha a partir do
 * cadastro, valida os pagamentos e devolve o resultado oficial; o navegador nunca decide valor.
 * Desconto e "Limpar" pedem a credencial (senha ou código de crachá) de quem tem permissão; o servidor revalida e audita (apps/pdv/autorizacao.py).
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
  // O select2 (dropdowns com pesquisa de todo o sistema) dispara "change" só pelo jQuery, que NÃO aciona addEventListener nativo.
  // jQuery.on recebe os dois tipos de evento; sem jQuery, usa o evento nativo.
  function escutarMudanca(select, fn) {
    if (window.jQuery) window.jQuery(select).on('change', fn); else select.addEventListener('change', fn);
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
        escutarMudanca(select, function () { l.forma = parseInt(select.value, 10); if (!formaDe(l.forma) || formaDe(l.forma).tipo !== 'dinheiro') l.recebido = 0; desenhar(); aoMudar(); });
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

  // Depois de finalizar, pergunta se emite a nota (só quando o servidor oferece: empresa emite, módulo habilitado, usuário pode).
  // A emissão é idempotente no servidor; sem provedor o documento fica pendente e o retorno explica o motivo. Devolve uma Promise.
  function perguntarNota(oferta) {
    if (!oferta || !window.Swal) return Promise.resolve();
    var tipos = oferta.tipos || [];
    var rotulos = { nfce: 'NFC-e', nfe: 'NF-e', nfse: 'NFS-e' };
    var cfg = { title: 'Emitir nota fiscal?', text: 'A venda já foi registrada. Deseja emitir ' + (tipos.length === 1 ? rotulos[tipos[0]] : 'a nota fiscal') + ' agora?', icon: 'question',
      showCancelButton: true, confirmButtonText: 'Sim, emitir', cancelButtonText: 'Não', reverseButtons: true, customClass: { confirmButton: 'btn btn-primary me-4', cancelButton: 'btn btn-label-secondary' }, buttonsStyling: false };
    if (tipos.length > 1) { cfg.input = 'select'; cfg.inputOptions = tipos.reduce(function (o, t) { o[t] = rotulos[t] || t; return o; }, {}); cfg.inputValue = tipos[0]; }
    return window.Swal.fire(cfg).then(function (r) {
      if (!r.isConfirmed) return;
      return enviar(oferta.url, { tipo: tipos.length > 1 ? r.value : tipos[0] }).then(function (res) {
        var ok = res.ok;
        return window.Swal.fire({ icon: ok ? 'info' : 'error', title: ok ? 'Nota fiscal ' + (res.dados.status_rotulo || '').toLowerCase() : 'Não foi possível emitir', text: ok ? (res.dados.motivo || 'Pedido de emissão registrado.') : (res.dados.erro || 'Tente novamente pela tela Fiscal.'),
          confirmButtonText: 'Entendi', customClass: { confirmButton: 'btn btn-primary' }, buttonsStyling: false });
      });
    });
  }

  // Credencial do desconto: pedida uma vez por tentativa de venda; `descartar()` força pedir de novo (ex.: servidor recusou).
  function CredencialDesconto() {
    var cred = null;
    return {
      obter: function (centavos) {
        if (cred) return Promise.resolve(cred);
        return window.PdvAutorizacao.pedir('Autorizar desconto de ' + moeda(centavos)).then(function (c) { cred = c; return c; });
      },
      descartar: function () { cred = null; },
    };
  }

  // ---------------- balcão ----------------
  function iniciarBalcao(app) {
    var formas = JSON.parse(document.getElementById('formasPdv').textContent);
    var carrinho = [], chave = novaChave(), enviando = false, descontoCentavos = 0, credDesconto = CredencialDesconto();
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

    // Excluir item lançado exige a senha de quem tem permissão (o servidor valida; a venda só é gravada ao finalizar).
    function excluirComSenha(item, idx) {
      window.PdvAutorizacao.pedir('Excluir "' + item.nome + '" da venda').then(function (cred) {
        if (!cred) return;
        enviar(app.dataset.urlAutorizar, { acao: 'remover_item', usuario: cred.usuario, senha: cred.senha, codigo: cred.codigo }).then(function (r) {
          if (r.ok) { carrinho.splice(idx, 1); desenharCarrinho(); } else { window.PdvAutorizacao.erro((r.dados && r.dados.erro) || 'Não foi possível autorizar.'); }
        });
      });
    }

    function desenharCarrinho() {
      var corpo = $('pdvCarrinho');
      corpo.innerHTML = '';
      carrinho.forEach(function (i, idx) {
        var qtd = el('input', { type: 'text', 'class': 'form-control form-control-sm text-end', style: 'width:5rem', value: String(i.qtd).replace('.', ','), inputmode: 'decimal', 'aria-label': 'Quantidade de ' + i.nome });
        qtd.addEventListener('change', function () { var v = parseFloat(qtd.value.replace(',', '.')); if (v > 0) { i.qtd = v; } desenharCarrinho(); });
        var rm = el('button', { type: 'button', 'class': 'btn btn-sm btn-text-danger', 'aria-label': 'Remover ' + i.nome, text: '✕' });
        rm.addEventListener('click', function () { excluirComSenha(i, idx); });
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
              (p.imagem_url ? el('img', { src: p.imagem_url, alt: '', width: 48, height: 48, 'class': 'rounded mb-1 d-block', style: 'object-fit:cover' }) : document.createTextNode('')), el('div', { 'class': 'fw-medium', text: p.nome }), el('small', { 'class': 'text-muted d-block', text: p.categoria + ' · ' + p.preco_formatado }),
              el('small', { 'class': 'd-block ' + (p.estoque && p.estoque.controlado && Number(p.estoque.saldo) <= 0 ? 'text-danger' : 'text-muted'), text: p.estoque ? p.estoque.rotulo : '' }),
            ]);
            b.addEventListener('click', function () { adicionar(p); });
            lista.appendChild(el('div', { 'class': 'col-6 col-lg-4' }, [b]));
          });
          if (!d.itens || !d.itens.length) lista.appendChild(el('div', { 'class': 'col-12 text-muted', text: 'Nenhum produto com preço do PDV encontrado.' }));
          // Catálogo maior que o limite da tela: avisa em vez de esconder o resto (a busca encontra qualquer produto).
          if (d.truncado) lista.appendChild(el('div', { 'class': 'col-12 small text-warning', text: 'Mostrando ' + d.itens.length + ' de ' + d.total + ' produtos. Use a busca para achar os demais.' }));
        });
    }
    // Leitor de código de barras/QR (digita o código e dá Enter) ou câmera: o produto com esse código entra direto no carrinho.
    function lerCodigo(codigo) {
      codigo = String(codigo || '').trim();
      if (!codigo) return;
      var msg = $('pdvLeitorMsg');
      fetch(app.dataset.urlProdutos + '?q=' + encodeURIComponent(codigo), { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var achado = (d.itens || []).filter(function (p) { return p.codigo === codigo; })[0];
          if (achado) {
            adicionar(achado); msg.className = 'small mb-2 text-success'; msg.textContent = achado.nome + ' adicionado.';
            $('pdvBusca').value = ''; buscar();
          } else if (d.itens && d.itens.length === 1) {
            // texto digitado que bate com um único produto pelo nome: Enter adiciona
            adicionar(d.itens[0]); msg.className = 'small mb-2 text-success'; msg.textContent = d.itens[0].nome + ' adicionado.'; $('pdvBusca').value = ''; buscar();
          } else {
            msg.className = 'small mb-2 text-danger'; msg.textContent = 'Nenhum produto com o código ou nome "' + codigo + '".';
            $('pdvBusca').select();
          }
        });
    }
    $('pdvBusca').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(temporizador); lerCodigo(this.value); } });
    document.addEventListener('codigo-lido', function (e) { lerCodigo(e.detail); });
    $('pdvBusca').addEventListener('input', function () { clearTimeout(temporizador); temporizador = setTimeout(buscar, 250); });
    $('pdvAddPagamento').addEventListener('click', function () { pag.adicionar(Math.max(0, total() - pag.pagos())); });
    document.querySelectorAll('[data-dividir]').forEach(function (b) { b.addEventListener('click', function () { pag.dividir(total(), parseInt(b.dataset.dividir, 10)); }); });
    if ($('pdvDesconto')) {
      $('pdvDesconto').addEventListener('input', function () { descontoCentavos = centavos(this.value); this.value = descontoCentavos ? moeda(descontoCentavos).replace('R$ ', '') : ''; pag.limpar(); pag.preencher(total()); atualizarTotais(); });
    }
    function limparTudo() { carrinho = []; descontoCentavos = 0; credDesconto.descartar(); if ($('pdvDesconto')) $('pdvDesconto').value = ''; pag.limpar(); chave = novaChave(); desenharCarrinho(); $('pdvMsg').textContent = ''; }
    // Limpar descarta a venda em andamento: mostra exatamente o que será apagado e exige a credencial de quem pode (validada e auditada no servidor).
    $('pdvLimpar').addEventListener('click', function () {
      if (!carrinho.length && !descontoCentavos) { limparTudo(); return; }  // nada a perder: não pede autorização
      var qtd = carrinho.reduce(function (n, i) { return n + i.qtd; }, 0);
      var resumo = carrinho.length + ' item(ns), ' + String(qtd).replace('.', ',') + ' un., total ' + moeda(total()) + (descontoCentavos ? ' (com desconto de ' + moeda(descontoCentavos) + ')' : '');
      window.PdvAutorizacao.pedir('Limpar a venda em andamento', 'Será apagado: ' + resumo + '.').then(function (cred) {
        if (!cred) return;
        enviar(app.dataset.urlAutorizar, { acao: 'limpar', usuario: cred.usuario, senha: cred.senha, codigo: cred.codigo, resumo: resumo }).then(function (r) {
          if (r.ok) limparTudo(); else window.PdvAutorizacao.erro((r.dados && r.dados.erro) || 'Não foi possível autorizar.');
        });
      });
    });

    $('pdvFinalizar').addEventListener('click', function () {
      if (enviando) return;
      enviando = true; atualizarTotais();
      // Desconto: pede (uma vez) a credencial de quem pode concedê-lo; o servidor valida de novo e audita.
      (descontoCentavos > 0 ? credDesconto.obter(descontoCentavos) : Promise.resolve(null)).then(function (cred) {
        if (descontoCentavos > 0 && !cred) { enviando = false; atualizarTotais(); return; }  // cancelou a autorização
        $('pdvMsg').className = 'alert alert-info mt-3'; $('pdvMsg').textContent = 'Registrando a venda...';
        var corpo = {
          itens: carrinho.map(function (i) { return { produto: i.produto, quantidade: i.qtd }; }), pagamentos: pag.dados(), desconto: descontoCentavos,
          observacao: $('pdvObs').value, consumidor: $('pdvConsumidor') ? $('pdvConsumidor').value : '', chave: chave,
        };
        if (descontoCentavos > 0) corpo.autorizacao = cred;
        return enviar(app.dataset.urlFinalizar, corpo).then(function (r) {
          enviando = false;
          if (!r.ok) { credDesconto.descartar(); $('pdvMsg').className = 'alert alert-danger mt-3'; $('pdvMsg').textContent = r.dados.erro || 'Não foi possível registrar a venda.'; atualizarTotais(); return; }
          $('pdvMsg').className = 'alert alert-success mt-3';
          $('pdvMsg').innerHTML = '';
          $('pdvMsg').appendChild(document.createTextNode('Venda #' + r.dados.numero + ' registrada: ' + r.dados.total_formatado + (r.dados.troco ? ' · troco ' + r.dados.troco_formatado : '') + '. '));
          var a = el('a', { href: r.dados.comprovante, target: '_blank', rel: 'noopener', text: 'Abrir comprovante' });
          $('pdvMsg').appendChild(a);
          carrinho = []; descontoCentavos = 0; credDesconto.descartar(); if ($('pdvDesconto')) $('pdvDesconto').value = ''; pag.limpar(); chave = novaChave(); desenharCarrinho();
          return perguntarNota(r.dados.nota_fiscal);
        });
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
    var totalBase = parseInt(app.dataset.total, 10), desconto = 0, chave = novaChave(), enviando = false, credDesconto = CredencialDesconto();
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
      if (enviando) return;
      enviando = true; atualizar();
      (desconto > 0 ? credDesconto.obter(desconto) : Promise.resolve(null)).then(function (cred) {
        if (desconto > 0 && !cred) { enviando = false; atualizar(); return; }
        var corpo = { pagamentos: pag.dados(), desconto: desconto, chave: chave };
        if (desconto > 0) corpo.autorizacao = cred;
        return enviar(app.dataset.urlPagar, corpo).then(function (r) {
          enviando = false;
          if (!r.ok) { credDesconto.descartar(); $('pdvMsg').className = 'alert alert-danger mt-3'; $('pdvMsg').textContent = r.dados.erro || 'Não foi possível receber.'; atualizar(); return; }
          return perguntarNota(r.dados.nota_fiscal).then(function () { window.location.href = r.dados.comprovante; });
        });
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
