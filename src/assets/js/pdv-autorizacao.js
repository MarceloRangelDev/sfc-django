/**
 * Autorização no PDV por senha OU código de crachá (barras/QR): excluir item lançado, limpar, cancelar venda/comanda,
 * devolver/extornar e conceder desconto.
 *
 * - <form data-autorizar="Título"> só é enviado depois de o usuário informar usuário + senha de quem tem a permissão;
 *   os valores vão nos campos usuario_autorizacao/senha_autorizacao ou codigo_autorizacao. O SERVIDOR valida (apps/pdv/autorizacao.py);
 *   aqui é só a coleta. Senha/código nunca são guardados: existem só enquanto o formulário é enviado.
 * - window.PdvAutorizacao.pedir(titulo, detalhe?) -> Promise<{usuario, senha, codigo} | null>, para telas que enviam por JavaScript.
 *   `detalhe` (texto simples) é exibido antes dos campos, p.ex. o que será descartado ao limpar.
 */
(function () {
  'use strict';

  var usuarioAtual = function () {
    var e = document.querySelector('[data-usuario-atual]');
    return e ? e.getAttribute('data-usuario-atual') : '';
  };

  function pedir(titulo, detalhe) {
    if (typeof window.Swal === 'undefined') {
      var senha = window.prompt((titulo || 'Autorização') + (detalhe ? '\n' + detalhe : '') + '\nSenha de quem tem permissão:');
      return Promise.resolve(senha ? { usuario: usuarioAtual(), senha: senha, codigo: '' } : null);
    }
    return window.Swal.fire({
      title: titulo || 'Autorização necessária',
      html:
        (detalhe ? '<p class="mb-2" id="pdvAutDetalhe"></p>' : '') +
        '<p class="text-muted small mb-3">Informe a senha de quem tem permissão para esta operação ou leia o código do crachá.</p>' +
        '<input id="pdvAutUsuario" class="swal2-input" placeholder="Usuário (supervisor)" autocomplete="off" value="' + usuarioAtual().replace(/"/g, '&quot;') + '">' +
        '<input id="pdvAutSenha" type="password" class="swal2-input" placeholder="Senha" autocomplete="new-password">' +
        '<input id="pdvAutCodigo" type="password" class="swal2-input" placeholder="ou código do crachá (leitor)" autocomplete="off">',
      focusConfirm: false, showCancelButton: true, confirmButtonText: 'Autorizar', cancelButtonText: 'Cancelar', reverseButtons: true,
      customClass: { confirmButton: 'btn btn-primary me-4', cancelButton: 'btn btn-label-secondary' }, buttonsStyling: false,
      didOpen: function () {
        var d = document.getElementById('pdvAutDetalhe'); if (d) d.textContent = detalhe;  // textContent: o detalhe nunca é interpretado como HTML
        var s = document.getElementById('pdvAutSenha'); if (s) s.focus();
        var c = document.getElementById('pdvAutCodigo');  // o leitor "digita" o código e dá Enter: confirma direto
        if (c) c.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); window.Swal.clickConfirm(); } });
      },
      preConfirm: function () {
        var usuario = document.getElementById('pdvAutUsuario').value.trim(), senha = document.getElementById('pdvAutSenha').value;
        var codigo = document.getElementById('pdvAutCodigo').value.trim();
        if (!senha && !codigo) { window.Swal.showValidationMessage('Digite a senha ou leia o código do crachá.'); return false; }
        return { usuario: usuario, senha: codigo ? '' : senha, codigo: codigo };
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
      campoOculto(form, 'codigo_autorizacao', cred.codigo || '');
      form.dataset.autorizado = '1';
      form.submit();  // envio nativo: não dispara este evento de novo
    });
  }, true);  // captura: roda antes do data-confirmar do alertas.js

  window.PdvAutorizacao = { pedir: pedir, erro: erro };
})();
