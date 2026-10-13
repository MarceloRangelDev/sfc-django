/**
 * Lista de precificações: lembra o formato escolhido (tabela ou cartões).
 *
 * O servidor desenha a lista conforme o parâmetro "visao" da URL. Este arquivo só guarda a
 * escolha no navegador (localStorage) e, quando a página abre sem o parâmetro, repete a
 * última escolha na URL — assim filtros, ordenação e paginação mantêm o formato. Se o
 * armazenamento não estiver disponível, a lista simplesmente abre em tabela.
 */
(function () {
  'use strict';
  var CHAVE = 'sfc.precificacao.visao';
  var VALIDAS = ['tabela', 'cartoes'];
  var url = new URL(window.location.href);

  function ler() {
    try { var v = window.localStorage.getItem(CHAVE); return VALIDAS.indexOf(v) >= 0 ? v : null; } catch (e) { return null; }
  }
  function gravar(valor) {
    try { window.localStorage.setItem(CHAVE, valor); } catch (e) { /* sem armazenamento: segue sem lembrar */ }
  }

  var naUrl = url.searchParams.get('visao');
  if (VALIDAS.indexOf(naUrl) >= 0) {
    gravar(naUrl);
  } else {
    var guardada = ler();
    if (guardada && guardada !== 'tabela') {
      url.searchParams.set('visao', guardada);
      window.location.replace(url.toString());
    }
  }
  document.querySelectorAll('[data-visao]').forEach(function (link) {
    link.addEventListener('click', function () { gravar(link.getAttribute('data-visao')); });
  });
})();
