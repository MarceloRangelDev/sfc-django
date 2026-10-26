/**
 * Ordem dos módulos no menu (Configurações > Módulos do cliente).
 *
 * Arrastar e soltar (SortableJS) OU as setas de cada linha (alternativa por teclado/leitor de tela). Cada mudança é enviada na hora
 * ao servidor (modulo-ordem), que valida permissão, cliente e lista de módulos e é quem grava. Se o salvamento falhar, a lista volta
 * à última ordem confirmada e o erro aparece num alerta: a ordem anterior nunca se perde em silêncio.
 */
(function () {
  'use strict';

  var cartao = document.getElementById('cartaoOrdem'), lista = document.getElementById('listaOrdem');
  if (!cartao || !lista) return;
  var vivo = document.getElementById('ordemVivo');
  var confirmada = ids();
  var salvando = false;

  function ids() { return Array.prototype.map.call(lista.children, function (li) { return li.getAttribute('data-id'); }); }
  function csrf() { var m = document.cookie.match(/csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ''; }
  function avisar(texto, erro) {
    if (window.Swal) window.Swal.fire({ icon: erro ? 'error' : 'success', title: erro ? 'Não foi possível salvar a ordem' : 'Ordem salva', text: texto, timer: erro ? undefined : 1500, showConfirmButton: !!erro, confirmButtonText: 'OK', customClass: { confirmButton: 'btn btn-primary' }, buttonsStyling: false });
    else if (erro) window.alert(texto);
  }
  // Reaplica uma ordem (lista de ids) ao DOM, sem recriar os itens (mantém os listeners).
  function aplicar(ordem) {
    var por = {};
    Array.prototype.forEach.call(lista.children, function (li) { por[li.getAttribute('data-id')] = li; });
    ordem.forEach(function (id) { if (por[id]) lista.appendChild(por[id]); });
  }

  function enviar(corpo) {
    return fetch(cartao.dataset.url, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'Accept': 'application/json' },
      body: JSON.stringify(cartao.dataset.cliente ? Object.assign({ cliente_id: cartao.dataset.cliente }, corpo) : corpo), credentials: 'same-origin',
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }

  function salvar() {
    var nova = ids();
    if (nova.join() === confirmada.join() || salvando) return;
    salvando = true;
    enviar({ modulos: nova }).then(function (r) {
      salvando = false;
      if (!r.ok) { aplicar(confirmada); avisar((r.d && r.d.erro) || 'Tente novamente.', true); return; }
      confirmada = r.d.modulos.map(function (m) { return String(m.id); });
      aplicar(confirmada);
      if (vivo) vivo.textContent = 'Ordem salva.';
      avisar('', false);
    }).catch(function () { salvando = false; aplicar(confirmada); avisar('Sem conexão com o servidor. A ordem anterior foi mantida.', true); });
  }

  if (window.Sortable) window.Sortable.create(lista, { handle: '.drag-handle', animation: 150, onEnd: salvar });

  lista.addEventListener('click', function (e) {
    var b = e.target.closest('[data-mover]');
    if (!b) return;
    var li = b.closest('[data-id]'), passo = parseInt(b.getAttribute('data-mover'), 10);
    var alvo = passo < 0 ? li.previousElementSibling : li.nextElementSibling;
    if (!alvo) return;
    if (passo < 0) lista.insertBefore(li, alvo); else lista.insertBefore(alvo, li);
    b.focus();
    salvar();
  });

  var restaurar = document.getElementById('restaurarOrdem');
  if (restaurar) restaurar.addEventListener('click', function () {
    enviar({ restaurar: true }).then(function (r) {
      if (!r.ok) { avisar((r.d && r.d.erro) || 'Tente novamente.', true); return; }
      confirmada = r.d.modulos.map(function (m) { return String(m.id); });
      aplicar(confirmada);
      avisar('', false);
    }).catch(function () { avisar('Sem conexão com o servidor.', true); });
  });
})();
