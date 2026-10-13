/**
 * Tour de página reutilizável (Shepherd).
 *
 * Uso, no template da página:
 *
 *   <script>
 *     initTour({
 *       page: 'insumos',                       // chave da página (preferência "não mostrar novamente")
 *       steps: [
 *         { element: '[data-tour="novo"]', title: 'Novo insumo', text: 'Cadastre um insumo.', position: 'bottom' },
 *         { element: 'table', title: 'Tabela', text: 'Seus insumos.' }
 *       ]
 *     });
 *   </script>
 *
 * - Abre sozinho enquanto o usuário não clicar em "Não mostrar novamente"
 *   (a preferência é gravada por usuário no servidor: apps/tour).
 * - O botão "?" (canto inferior direito) inicia o tour manualmente quando quiser.
 * - Passos cujo elemento não existe na página (ex.: botão sem permissão) são ignorados.
 */
(function () {
  'use strict';

  var raiz = document.documentElement.getAttribute('data-assets-path') || '/static/';

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  // O Shepherd só é baixado nas páginas que têm tour.
  function carregarShepherd() {
    if (window.Shepherd) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      var css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = raiz + 'vendor/libs/shepherd/shepherd.css';
      document.head.appendChild(css);
      var js = document.createElement('script');
      js.src = raiz + 'vendor/libs/shepherd/shepherd.js';
      js.onload = resolve;
      js.onerror = reject;
      document.head.appendChild(js);
    });
  }

  function ocultarParaSempre(pagina) {
    return fetch('/tour/' + encodeURIComponent(pagina) + '/ocultar', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf() },
      credentials: 'same-origin'
    });
  }

  function criarTour(config) {
    var passos = config.steps.filter(function (p) { return document.querySelector(p.element); });
    if (!passos.length) return null;

    var tour = new window.Shepherd.Tour({
      useModalOverlay: true,
      defaultStepOptions: { cancelIcon: { enabled: true }, scrollTo: { behavior: 'smooth', block: 'center' } }
    });
    var classeSecundario = 'btn btn-sm btn-label-secondary';
    var classePrimario = 'btn btn-sm btn-primary';

    passos.forEach(function (passo, i) {
      var ultimo = i === passos.length - 1;
      var botoes = [
        {
          text: 'Não mostrar novamente',
          classes: classeSecundario,
          action: function () { ocultarParaSempre(config.page); tour.cancel(); }
        }
      ];
      if (i > 0) botoes.push({ text: 'Voltar', classes: classeSecundario, action: tour.back });
      botoes.push({ text: ultimo ? 'Concluir' : 'Próximo', classes: classePrimario, action: ultimo ? tour.complete : tour.next });

      tour.addStep({
        id: config.page + '-' + i,
        title: passo.title,
        text: passo.text,
        attachTo: { element: passo.element, on: passo.position || 'bottom' },
        buttons: botoes
      });
    });
    return tour;
  }

  function botaoDeAjuda(aoClicar) {
    var botao = document.createElement('button');
    botao.type = 'button';
    botao.className = 'btn btn-icon btn-primary rounded-pill shadow';
    botao.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:1040;';
    botao.title = 'Tour desta página';
    botao.setAttribute('aria-label', 'Iniciar o tour desta página');
    botao.innerHTML = '<i class="icon-base ti tabler-help"></i>';
    botao.addEventListener('click', aoClicar);
    document.body.appendChild(botao);
  }

  window.initTour = function (config) {
    function iniciar() {
      carregarShepherd().then(function () {
        var tour = criarTour(config);
        if (!tour) return;
        botaoDeAjuda(function () {
          // Recria o tour para recomeçar do primeiro passo.
          var novo = criarTour(config);
          if (novo) novo.start();
        });
        if (config.auto === false) return;
        fetch('/tour/' + encodeURIComponent(config.page) + '/', { credentials: 'same-origin' })
          .then(function (r) { return r.json(); })
          .then(function (estado) { if (!estado.ocultar) tour.start(); })
          .catch(function () { /* sem rede: o tour continua disponível pelo botão "?" */ });
      });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
    else iniciar();
  };
})();
