/**
 * Inscrição de web push do navegador (django-webpush).
 *
 * Só é carregado quando as chaves VAPID estão configuradas. Se o usuário já
 * liberou as notificações, a inscrição é reenviada em silêncio; senão o botão
 * "Ativar notificações no navegador" (menu do sino) pede a permissão.
 */
(function () {
  'use strict';

  var botao = document.getElementById('btn-ativar-push');
  if (!botao || !('serviceWorker' in navigator) || !('PushManager' in window)) return;

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function chaveParaBytes(base64) {
    var preenchida = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    var bruto = window.atob(preenchida);
    return Uint8Array.from(bruto, function (c) { return c.charCodeAt(0); });
  }

  function enviarInscricao(inscricao) {
    return fetch('/notificacoes/inscricao-push', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      credentials: 'same-origin',
      body: JSON.stringify({ subscription: inscricao.toJSON(), browser: navigator.userAgent.slice(0, 100) })
    });
  }

  function inscrever(registro) {
    return registro.pushManager
      .getSubscription()
      .then(function (atual) {
        return atual || registro.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: chaveParaBytes(botao.dataset.vapidKey)
        });
      })
      .then(enviarInscricao);
  }

  navigator.serviceWorker.register('/service-worker.js').then(function (registro) {
    if (Notification.permission === 'granted') {
      inscrever(registro);
    } else if (Notification.permission === 'default') {
      botao.hidden = false;
      botao.addEventListener('click', function () {
        Notification.requestPermission().then(function (permissao) {
          if (permissao === 'granted') inscrever(registro).then(function () { botao.hidden = true; });
        });
      });
    }
  });
})();
