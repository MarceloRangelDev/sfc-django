/**
 * Autorização por senha no PDV (excluir item lançado, cancelar venda/comanda, devolver/extornar).
 *
 * - <form data-autorizar="Título"> só é enviado depois de o usuário informar usuário + senha de quem tem a permissão;
 *   os dois valores vão nos campos usuario_autorizacao/senha_autorizacao. O SERVIDOR valida (apps/pdv/autorizacao.py);
 *   aqui é só a coleta. A senha nunca é guardada: existe só enquanto o formulário é enviado.
 * - window.PdvAutorizacao.pedir(titulo) -> Promise<{usuario, senha} | null>, para telas que enviam por JavaScript.
 */
(function () {
  'use strict';

  var usuarioAtual = function () {
    var e = document.querySelector('[data-usuario-atual]');
    return e ? e.getAttribute('data-usuario-atual') : '';
  };

  function pedir(titulo) {
    if (typeof window.Swal === 'undefined') {
      var senha = window.prompt((titulo || 'Autorização') + '\nSenha de quem tem permissão:');
      return Promise.resolve(senha ? { usuario: usuarioAtual(), senha: senha } : null);
    }
    return window.Swal.fire({
      title: titulo || 'Autorização necessária',
      html:
        '<p class="text-muted small mb-3">Informe a senha de quem tem permissão para esta operação.</p>' +
        '<input id="pdvAutUsuario" class="swal2-input" placeholder="Usuário (supervisor)" autocomplete="off" value="' + usuarioAtual().replace(/"/g, '&quot;') + '">' +
        '<input id="pdvAutSenha" type="password" class="swal2-input" placeholder="Senha" autocomplete="new-password">',
      focusConfirm: false, showCancelButton: true, confirmButtonText: 'Autorizar', cancelButtonText: 'Cancelar', reverseButtons: true,
      customClass: { confirmButton: 'btn btn-primary me-4', cancelButton: 'btn btn-label-secondary' }, buttonsStyling: false,
      didOpen: function () { var s = document.getElementById('pdvAutSenha'); if (s) s.focus(); },
      preConfirm: function () {
        var usuario = document.getElementById('pdvAutUsuario').value.trim(), senha = document.getElementById('pdvAutSenha').value;
        if (!senha) { window.Swal.showValidationMessage('Digite a senha.'); return false; }
        return { usuario: usuario, senha: senha };
      }
    }).then(function (r) { return r.isConfirmed ? r.value : null; });
  }

  function erro(texto) {
    if (typeof window.Swal === 'undefined') { window.alert(texto); return; }
    window.Swal.fire({ icon: 'error', title: 'Não autorizado', text: texto, confirmButtonText: 'Entendi', customClass: { confirmButton: 'btn btn-primary' }, buttonsStyling: false });
  }

  function campoOculto(form, nome, valor) {
    var c = form.querySelector('input[name="' + nome + '"]');
    if (!c) { c = document.createElement('input'); c.type = 'hidden'; c.name = nome; form.appendChild(c); }
    c.value = valor;
  }

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('form[data-autorizar]') || form.dataset.autorizado === '1') return;
    e.preventDefault();
    pedir(form.getAttribute('data-autorizar')).then(function (cred) {
      if (!cred) return;
      campoOculto(form, 'usuario_autorizacao', cred.usuario);
      campoOculto(form, 'senha_autorizacao', cred.senha);
      form.dataset.autorizado = '1';
      form.submit();  // envio nativo: não dispara este evento de novo
    });
  }, true);  // captura: roda antes do data-confirmar do alertas.js

  window.PdvAutorizacao = { pedir: pedir, erro: erro };
})();
