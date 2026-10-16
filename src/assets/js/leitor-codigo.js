/**
 * Leitura de código de barras / QR Code pela câmera (botão "Câmera" do PDV).
 * Leitores USB/Bluetooth não precisam disto: eles "digitam" o código no campo em foco e dão Enter.
 * Usa a API nativa BarcodeDetector (Chrome/Edge/Android); sem suporte, o botão fica escondido.
 * Ao ler, dispara o evento `codigo-lido` (detail = texto) e, na comanda, preenche e envia o formulário.
 */
(function () {
  'use strict';
  var botao = document.getElementById('leitorCamera');
  if (!botao || !('BarcodeDetector' in window) || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  botao.classList.remove('d-none');

  var ativo = null;
  function fechar() {
    if (!ativo) return;
    ativo.parar = true;
    ativo.stream.getTracks().forEach(function (t) { t.stop(); });
    ativo.painel.remove();
    ativo = null;
  }

  function entregar(texto) {
    fechar();
    var form = document.getElementById('formLeitor');
    if (form) { document.getElementById('leitorCodigo').value = texto; form.submit(); return; }
    document.dispatchEvent(new CustomEvent('codigo-lido', { detail: texto }));
  }

  botao.addEventListener('click', function () {
    if (ativo) return;
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false }).then(function (stream) {
      var video = document.createElement('video');
      video.setAttribute('playsinline', ''); video.muted = true; video.srcObject = stream;
      video.style.cssText = 'width:100%;max-height:60vh;background:#000;border-radius:8px';
      var cancelar = document.createElement('button');
      cancelar.type = 'button'; cancelar.className = 'btn btn-secondary mt-2'; cancelar.textContent = 'Cancelar';
      var painel = document.createElement('div');
      painel.style.cssText = 'position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;padding:16px';
      var caixa = document.createElement('div');
      caixa.style.cssText = 'max-width:480px;width:100%;background:#fff;border-radius:10px;padding:12px;text-align:center';
      var dica = document.createElement('p'); dica.className = 'mb-2 text-dark'; dica.textContent = 'Aponte a câmera para o código de barras ou QR Code.';
      caixa.appendChild(dica); caixa.appendChild(video); caixa.appendChild(cancelar); painel.appendChild(caixa); document.body.appendChild(painel);
      ativo = { stream: stream, painel: painel, parar: false };
      cancelar.addEventListener('click', fechar);
      video.play();
      var detector = new BarcodeDetector();
      (function ciclo() {
        if (!ativo || ativo.parar) return;
        detector.detect(video).then(function (achados) {
          if (achados && achados.length && achados[0].rawValue) entregar(achados[0].rawValue); else setTimeout(ciclo, 250);
        }).catch(function () { setTimeout(ciclo, 400); });
      })();
    }).catch(function () { alert('Não foi possível acessar a câmera. Verifique a permissão do navegador.'); });
  });
})();
