/**
 * Alertas, avisos e confirmações do sistema — um único ponto, via SweetAlert2.
 *
 *  1. Mensagens do Django (messages.success/info/warning/error): o servidor as entrega
 *     escondidas em #mensagens-django (templates/layout/master.html) e este arquivo as
 *     exibe: sucesso e erro/aviso viram caixas CENTRALIZADAS na tela (o sucesso some sozinho
 *     em 5 s; o erro espera um clique); informação vira um aviso rápido no canto. Nenhuma
 *     página desenha seus próprios alertas.
 *  2. Confirmações: um <form data-confirmar="Pergunta?"> só é enviado depois que o usuário
 *     confirma. Opcionais: data-confirmar-titulo e data-confirmar-botao ("Excluir").
 *  3. API para scripts de página: SFC.alerta(tipo, titulo, texto), SFC.confirmar({...}).
 *
 * Todos os textos aqui estão em português. Sem SweetAlert2 carregado, cai nos alertas do
 * navegador (a confirmação nunca é pulada).
 */
(function () {
  'use strict';

  var temSwal = function () { return typeof window.Swal !== 'undefined'; };
  var TITULOS = { success: 'Tudo certo!', info: 'Informação', warning: 'Atenção', error: 'Não foi possível concluir' };
  var ICONES = { success: 'success', info: 'info', warning: 'warning', error: 'error', debug: 'info' };
  var BOTAO = { confirmButton: 'btn btn-primary me-4', cancelButton: 'btn btn-label-secondary', denyButton: 'btn btn-danger me-4' };

  function aviso(tipo, texto) {
    // Sucesso: caixa CENTRALIZADA na tela, na mesma posição do erro, que some sozinha em 5 s (com barra de
    // progresso) e também fecha por botão, clique fora ou Esc. Informação continua como aviso rápido no canto.
    if (tipo === 'success') {
      return window.Swal.fire({
        icon: 'success', title: TITULOS.success, text: texto, confirmButtonText: 'OK',
        timer: 5000, timerProgressBar: true, customClass: BOTAO, buttonsStyling: false
      });
    }
    return window.Swal.fire({
      toast: true, position: 'top-end', icon: ICONES[tipo] || 'info', title: texto,
      showConfirmButton: false, timer: 5000, timerProgressBar: true, customClass: { popup: 'shadow' }
    });
  }

  function caixa(tipo, titulo, texto) {
    // Caixa que exige um clique: avisos e erros.
    return window.Swal.fire({
      icon: ICONES[tipo] || 'info', title: titulo || TITULOS[tipo], text: texto, confirmButtonText: 'Entendi',
      customClass: BOTAO, buttonsStyling: false
    });
  }

  function alerta(tipo, titulo, texto) {
    if (!temSwal()) { window.alert((titulo ? titulo + '\n' : '') + (texto || '')); return; }
    if (tipo === 'success' || tipo === 'info') { aviso(tipo, texto || titulo); } else { caixa(tipo, titulo, texto); }
  }

  /** Devolve uma Promise<boolean>: true se o usuário confirmou. */
  function confirmar(opcoes) {
    opcoes = opcoes || {};
    if (!temSwal()) { return Promise.resolve(window.confirm(opcoes.texto || 'Deseja continuar?')); }
    return window.Swal.fire({
      icon: opcoes.icone || 'warning', title: opcoes.titulo || 'Confirmar ação', text: opcoes.texto || 'Deseja continuar?',
      showCancelButton: true, focusCancel: true, reverseButtons: true,
      confirmButtonText: opcoes.botao || 'Confirmar', cancelButtonText: 'Cancelar',
      customClass: { confirmButton: 'btn btn-danger me-4', cancelButton: 'btn btn-label-secondary' }, buttonsStyling: false
    }).then(function (r) { return !!r.isConfirmed; });
  }

  // 1) mensagens vindas do servidor
  function mostrarMensagensDoServidor() {
    var origem = document.getElementById('mensagens-django');
    if (!origem) return;
    var rapidas = [], bloqueantes = [];
    origem.querySelectorAll('[data-tipo]').forEach(function (el) {
      var item = { tipo: el.getAttribute('data-tipo'), texto: el.textContent.trim() };
      if (!item.texto) return;
      (item.tipo === 'success' || item.tipo === 'info' ? rapidas : bloqueantes).push(item);
    });
    origem.remove();
    if (!temSwal()) { return; }
    // erros/avisos primeiro (precisam de leitura); os avisos rápidos aparecem em seguida
    var fila = Promise.resolve();
    bloqueantes.forEach(function (m) { fila = fila.then(function () { return caixa(m.tipo, null, m.texto); }); });
    // sucessos centralizados aparecem um de cada vez (como as caixas de erro); informações são avisos do canto
    rapidas.forEach(function (m) { fila = fila.then(function () { return aviso(m.tipo, m.texto); }); });
  }

  // 2) confirmações declarativas
  var confirmados = new WeakSet();
  document.addEventListener('submit', function (evento) {
    var form = evento.target;
    if (!form || !form.hasAttribute || !form.hasAttribute('data-confirmar') || confirmados.has(form)) return;
    evento.preventDefault();
    var botaoClicado = evento.submitter || null;
    confirmar({
      texto: form.getAttribute('data-confirmar'), titulo: form.getAttribute('data-confirmar-titulo') || undefined,
      botao: form.getAttribute('data-confirmar-botao') || 'Sim, excluir'
    }).then(function (ok) {
      if (!ok) return;
      confirmados.add(form);  // não pergunta de novo no envio real
      if (form.requestSubmit) { form.requestSubmit(botaoClicado); } else { form.submit(); }
    });
  });

  window.SFC = Object.assign(window.SFC || {}, { alerta: alerta, confirmar: confirmar });
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', mostrarMensagensDoServidor); }
  else { mostrarMensagensDoServidor(); }
})();
