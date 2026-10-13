/**
 * Evita duplo envio em formulários de cadastro: ao enviar, desabilita os
 * botões marcados com data-botao-salvar. Como um botão desabilitado não é
 * enviado, o botão clicado (ex.: "Salvar e adicionar outro") é guardado
 * antes num campo oculto com o mesmo nome/valor.
 *
 * Uso: <form data-form-salvar> ... <button data-botao-salvar name="salvar_e_novo" value="1">
 * (templates/partials/botoes_salvar.html já marca os botões).
 */
(function () {
  'use strict';
  document.querySelectorAll('form[data-form-salvar]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (e.submitter && e.submitter.name) {
        var oculto = document.createElement('input');
        oculto.type = 'hidden';
        oculto.name = e.submitter.name;
        oculto.value = e.submitter.value;
        form.appendChild(oculto);
      }
      form.querySelectorAll('[data-botao-salvar]').forEach(function (b) { b.disabled = true; });
    });
  });
})();
