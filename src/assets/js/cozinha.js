/**
 * Quadro da cozinha: fila de pedidos em quatro colunas (Novo, Confirmado, Em preparo, Pronto). Atualiza sozinho por push (SSE:
 * `cozinha-api-eventos` avisa que algo mudou e a tela busca a fila); uma consulta lenta de segurança cobre falha do stream.
 *
 * Atalhos (não substituem os botões, que continuam acessíveis por teclado/leitor de tela):
 *   ↑ / ↓ (ou K / J)  seleciona o pedido anterior / seguinte     → ou Enter  avança o pedido selecionado
 *   ←                 volta um passo                              C            abre o pedido (cancelar exige confirmação lá)
 *   ?                 mostra/oculta a ajuda
 * Operações críticas pedem confirmação: finalizar (entrega; não tem "desfazer") e cancelar (motivo obrigatório na tela do pedido).
 * O retorno de Finalizar (sucesso ou erro) aparece num SweetAlert.
 * O servidor valida TODA transição e permissão; a tela só pede e mostra o resultado.
 */
(function () {
  'use strict';

  var app = document.getElementById('cozinhaApp');
  if (!app) return;
  var urlFila = app.dataset.urlFila, urlMover = app.dataset.urlMover, urlPedido = app.dataset.urlPedido, urlEventos = app.dataset.urlEventos;
  var podeMover = app.dataset.podeMover === '1';
  var colunas = { novo: document.getElementById('colNovo'), confirmado: document.getElementById('colConfirmado'), em_preparo: document.getElementById('colPreparo'), pronto: document.getElementById('colPronto') };
  var aviso = document.getElementById('cozinhaAviso'), vivo = document.getElementById('cozinhaVivo');
  var pedidos = [], selecionado = null, ocupado = false;

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function el(tag, attrs, filhos) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    (filhos || []).forEach(function (f) { e.appendChild(f); });
    return e;
  }
  function anunciar(texto) { vivo.textContent = texto; }

  function swalDisponivel() { return typeof window.Swal !== 'undefined'; }
  function alerta(icone, titulo, texto) {
    if (!swalDisponivel()) { mostrarAviso(titulo + ' ' + texto, icone === 'error'); return Promise.resolve(); }
    return window.Swal.fire({ icon: icone, title: titulo, text: texto, confirmButtonText: 'OK', customClass: { confirmButton: 'btn btn-primary' }, buttonsStyling: false });
  }
  // Finalizar (pronto -> entregue) não tem "desfazer": confirma antes, e o retorno do servidor aparece no SweetAlert.
  function confirmarFinalizar(pedido) {
    if (!swalDisponivel()) return Promise.resolve(window.confirm('Finalizar o pedido #' + pedido.numero + ' (entregue)? Não há como desfazer.'));
    return window.Swal.fire({ icon: 'question', title: 'Finalizar o pedido #' + pedido.numero + '?', text: 'Marca como entregue. Não há como desfazer.', showCancelButton: true, confirmButtonText: 'Finalizar', cancelButtonText: 'Cancelar', reverseButtons: true,
      customClass: { confirmButton: 'btn btn-primary me-4', cancelButton: 'btn btn-label-secondary' }, buttonsStyling: false }).then(function (r) { return r.isConfirmed; });
  }

  function mover(pedido, acao) {
    if (!podeMover || ocupado) return;
    var finalizar = acao === 'avancar' && pedido.status === 'pronto';
    (finalizar ? confirmarFinalizar(pedido) : Promise.resolve(true)).then(function (seguir) {
      if (!seguir) return;
      ocupado = true;
      fetch(urlMover.replace('/0/', '/' + pedido.id + '/'), {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'Accept': 'application/json' }, body: JSON.stringify({ acao: acao }), credentials: 'same-origin',
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (r) {
          ocupado = false;
          if (!r.ok) {
            var msg = r.d.erro || 'Não foi possível mover o pedido.';
            if (finalizar) alerta('error', 'Não foi possível finalizar', msg); else mostrarAviso(msg, true);
            carregar(); return;
          }
          anunciar(r.d.mensagem || ('Pedido ' + r.d.pedido.numero + ' agora ' + r.d.pedido.status_rotulo.toLowerCase()));
          if (finalizar) alerta('success', 'Pedido finalizado', r.d.mensagem || 'Pedido entregue.');
          carregar();
        }).catch(function () {
          ocupado = false;
          if (finalizar) alerta('error', 'Sem conexão', 'Não foi possível falar com o servidor. Tente de novo.'); else mostrarAviso('Sem conexão com o servidor.', true);
        });
    });
  }

  function mostrarAviso(texto, erro) {
    aviso.textContent = texto; aviso.className = 'alert ' + (erro ? 'alert-danger' : 'alert-info') + ' py-2'; aviso.style.display = texto ? '' : 'none';
  }

  function cartao(p) {
    var cab = el('div', { 'class': 'd-flex justify-content-between align-items-start' }, [
      el('strong', { text: '#' + p.numero + ' ' + (p.mesa || p.tipo_rotulo) }),
      el('span', { 'class': 'badge ' + (p.minutos >= 20 ? 'bg-danger' : (p.minutos >= 10 ? 'bg-warning' : 'bg-label-secondary')), text: p.minutos + ' min' }),
    ]);
    var lista = el('ul', { 'class': 'list-unstyled mb-2 mt-2' }, p.itens.map(function (i) {
      var li = el('li', { text: i.quantidade + '× ' + i.descricao });
      if (i.observacao) li.appendChild(el('div', { 'class': 'small text-muted', text: '↳ ' + i.observacao }));
      return li;
    }));
    var corpo = [cab];
    if (p.cliente) corpo.push(el('div', { 'class': 'small text-muted', text: p.cliente }));
    corpo.push(lista);
    if (p.observacao) corpo.push(el('div', { 'class': 'small fst-italic mb-2', text: p.observacao }));
    var botoes = el('div', { 'class': 'd-flex gap-1 flex-wrap' });
    if (podeMover && p.pode_voltar) {
      var v = el('button', { type: 'button', 'class': 'btn btn-sm btn-outline-secondary', 'aria-label': 'Voltar o pedido ' + p.numero, text: '◂ Voltar' });
      v.addEventListener('click', function (e) { e.stopPropagation(); mover(p, 'voltar'); });
      botoes.appendChild(v);
    }
    if (podeMover && p.pode_avancar) {
      var rot = { novo: 'Confirmar ▸', confirmado: 'Iniciar preparo ▸', em_preparo: 'Pronto ▸', pronto: 'Finalizar ▸' }[p.status];
      var a = el('button', { type: 'button', 'class': 'btn btn-sm btn-primary', 'aria-label': rot.replace(' ▸', '') + ' — pedido ' + p.numero, text: rot });
      a.addEventListener('click', function (e) { e.stopPropagation(); mover(p, 'avancar'); });
      botoes.appendChild(a);
    }
    var det = el('a', { href: urlPedido.replace('/0', '/' + p.id), 'class': 'btn btn-sm btn-text-secondary', text: 'Abrir' });
    botoes.appendChild(det);
    corpo.push(botoes);
    var c = el('div', { 'class': 'card mb-2' + (p.id === selecionado ? ' border-primary border-2' : ''), role: 'listitem', tabindex: '0', 'data-id': p.id, 'aria-label': 'Pedido ' + p.numero + ', ' + p.status_rotulo }, [el('div', { 'class': 'card-body p-3' }, corpo)]);
    c.addEventListener('click', function () { selecionado = p.id; desenhar(); });
    return c;
  }

  function desenhar() {
    Object.keys(colunas).forEach(function (k) { colunas[k].innerHTML = ''; });
    var contagem = { novo: 0, confirmado: 0, em_preparo: 0, pronto: 0 };
    pedidos.forEach(function (p) {
      if (!colunas[p.status]) return;
      colunas[p.status].appendChild(cartao(p)); contagem[p.status]++;
    });
    Object.keys(colunas).forEach(function (k) {
      if (!contagem[k]) colunas[k].appendChild(el('div', { 'class': 'text-muted text-center py-4', text: 'Nenhum pedido' }));
      var t = document.getElementById('cont-' + k); if (t) t.textContent = contagem[k];
    });
  }

  function carregar() {
    fetch(urlFila, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('http'); return r.json(); })
      .then(function (d) {
        pedidos = d.pedidos;
        if (selecionado !== null && !pedidos.some(function (p) { return p.id === selecionado; })) selecionado = null;
        mostrarAviso('', false); desenhar();
      }).catch(function () { mostrarAviso('Sem conexão com o servidor: mostrando a última fila carregada.', true); });
  }

  function indiceSelecionado() { return pedidos.findIndex(function (p) { return p.id === selecionado; }); }
  document.addEventListener('keydown', function (e) {
    var alvo = e.target;
    if (alvo && (alvo.tagName === 'INPUT' || alvo.tagName === 'TEXTAREA' || alvo.tagName === 'SELECT' || alvo.isContentEditable)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var i = indiceSelecionado(), p = i >= 0 ? pedidos[i] : null;
    if (e.key === 'ArrowDown' || e.key === 'j' || e.key === 'J') { selecionado = pedidos.length ? pedidos[Math.min(pedidos.length - 1, i + 1)].id : null; desenhar(); e.preventDefault(); }
    else if (e.key === 'ArrowUp' || e.key === 'k' || e.key === 'K') { selecionado = pedidos.length ? pedidos[Math.max(0, i - 1)].id : null; desenhar(); e.preventDefault(); }
    else if (p && (e.key === 'ArrowRight' || (e.key === 'Enter' && !(alvo && alvo.tagName === 'BUTTON')))) { if (p.pode_avancar) mover(p, 'avancar'); e.preventDefault(); }
    else if (p && e.key === 'ArrowLeft') { if (p.pode_voltar) mover(p, 'voltar'); e.preventDefault(); }
    else if (p && (e.key === 'c' || e.key === 'C')) { window.location.href = urlPedido.replace('/0', '/' + p.id); }
    else if (e.key === '?') { var aj = document.getElementById('cozinhaAjuda'); aj.style.display = aj.style.display === 'none' ? '' : 'none'; }
  });

  // Push: o servidor avisa quando a fila muda (pedido novo, enviado da comanda, movido por outra tela...). O EventSource reconecta sozinho
  // ao fim de cada conexão; o 1º evento de cada conexão é só o estado atual (a tela já carregou), então só recarrega quando a marca muda.
  var ultimaVersao = null;
  function ouvirEventos() {
    if (!urlEventos || typeof window.EventSource === 'undefined') return;
    var fonte = new window.EventSource(urlEventos);
    fonte.addEventListener('fila', function (e) {
      var v = (JSON.parse(e.data) || {}).v;
      if (ultimaVersao !== null && v !== ultimaVersao) carregar();
      ultimaVersao = v;
    });
  }

  carregar();
  ouvirEventos();
  setInterval(function () { if (!document.hidden) carregar(); }, 30000);  // rede de segurança: o push é o caminho normal
})();
