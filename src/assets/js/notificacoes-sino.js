/**
 * Atualização automática do sino de notificações (navbar).
 *
 * Consulta /notificacoes/resumo periodicamente (só com a aba visível) e troca
 * contador, ponto e lista pelo HTML renderizado no servidor. O servidor sempre
 * responde com as notificações do próprio usuário logado. Se a consulta falhar
 * ou a sessão expirar, o sino continua como foi renderizado na página.
 */
(function () {
  'use strict';

  var sino = document.getElementById('sino-notificacoes');
  if (!sino || !window.fetch) return;

  var INTERVALO = 20000; // 20 s
  var INTERVALO_MAXIMO = 300000; // espera máxima após falhas seguidas
  var ponto = document.getElementById('sino-ponto');
  var contador = document.getElementById('sino-contador');
  var lista = document.getElementById('sino-lista');
  var temporizador = null;
  var emAndamento = false;
  var falhas = 0;
  var ultimo = null;

  // Com mais de 3 itens a lista limita a altura à de 3 itens e rola; com 3 ou menos fica na altura natural.
  // A altura é medida (não fixa em CSS) porque depende do tema/idioma/tamanho do texto, e só dá para medir com o menu aberto.
  var ITENS_SEM_ROLAGEM = 3;
  function ajustarRolagem() {
    var itens = lista.children;
    lista.style.maxHeight = '';
    if (itens.length <= ITENS_SEM_ROLAGEM) return;
    var altura = 0;
    for (var i = 0; i < ITENS_SEM_ROLAGEM; i++) altura += itens[i].offsetHeight;
    if (altura > 0) lista.style.maxHeight = altura + 'px'; // 0 = menu fechado: refeito em shown.bs.dropdown
  }
  sino.addEventListener('shown.bs.dropdown', ajustarRolagem);

  function aplicar(dados) {
    var assinatura = dados.nao_lidas + '|' + dados.html;
    if (assinatura === ultimo) return; // nada mudou: evita redesenhar à toa
    ultimo = assinatura;
    contador.textContent = dados.nao_lidas;
    ponto.hidden = !dados.nao_lidas;
    lista.innerHTML = dados.html; // HTML escapado e gerado pelo servidor (notificacoes_itens.html)
    ajustarRolagem();
  }

  function agendar() {
    clearTimeout(temporizador);
    if (document.hidden) return; // retomado em visibilitychange
    var espera = Math.min(INTERVALO * Math.pow(2, falhas), INTERVALO_MAXIMO);
    temporizador = setTimeout(consultar, espera);
  }

  function consultar() {
    if (emAndamento) return;
    emAndamento = true;
    fetch(sino.dataset.url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (resposta) {
        var tipo = resposta.headers.get('Content-Type') || '';
        if (!resposta.ok || tipo.indexOf('application/json') === -1) throw new Error('indisponivel');
        return resposta.json();
      })
      .then(function (dados) {
        falhas = 0;
        aplicar(dados);
      })
      .catch(function () {
        falhas = Math.min(falhas + 1, 4);
      })
      .then(function () {
        emAndamento = false;
        agendar();
      });
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      clearTimeout(temporizador);
    } else {
      consultar(); // ao voltar para a aba, atualiza na hora
    }
  });

  ajustarRolagem();
  agendar();
})();
