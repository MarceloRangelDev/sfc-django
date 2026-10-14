/**
 * Máscaras reutilizáveis (frontend) — uso:  <input data-mask="cpf">
 *
 * Tipos: cpf, cnpj, cpf-cnpj (decide pelo tamanho), telefone, celular, fone, cep,
 *        moeda, decimal, inteiro, data, datahora.
 *
 * IMPORTANTE: a máscara só melhora a digitação. TODA validação é refeita
 * no backend (web_project/regras/validadores.py e dinheiro.py) — nunca
 * confie só no que o navegador envia.
 *
 * Para um campo criado depois (ex.: modal): SFCMascaras.aplicar(elemento).
 */
(function (global) {
  'use strict';

  var soDigitos = function (t) { return (t || '').replace(/\D/g, ''); };

  // Aplica um padrão onde "9" é um dígito: aplicarPadrao('12345678909', '999.999.999-99')
  function aplicarPadrao(digitos, padrao) {
    var saida = '', i = 0;
    for (var p = 0; p < padrao.length && i < digitos.length; p++) {
      saida += padrao[p] === '9' ? digitos[i++] : padrao[p];
    }
    return saida;
  }

  var formatadores = {
    cpf: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 11), '999.999.999-99'); },
    cnpj: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 14), '99.999.999/9999-99'); },
    'cpf-cnpj': function (t) {
      var d = soDigitos(t);
      return d.length > 11 ? formatadores.cnpj(d) : formatadores.cpf(d);
    },
    telefone: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 10), '(99) 9999-9999'); },
    celular: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 11), '(99) 99999-9999'); },
    // fone: fixo (10 dígitos) ou celular (11), decidido pelo tamanho; cep: 00000-000.
    fone: function (t) { var d = soDigitos(t).slice(0, 11); return d.length > 10 ? formatadores.celular(d) : formatadores.telefone(d); },
    cep: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 8), '99999-999'); },
    data: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 8), '99/99/9999'); },
    datahora: function (t) { return aplicarPadrao(soDigitos(t).slice(0, 12), '99/99/9999 99:99'); },
    inteiro: function (t) { return soDigitos(t); },
    // Decimal brasileiro: só dígitos e UMA vírgula ('12,5').
    decimal: function (t) {
      var limpo = (t || '').replace(/[^\d,]/g, '');
      var i = limpo.indexOf(',');
      return i === -1 ? limpo : limpo.slice(0, i + 1) + limpo.slice(i + 1).replace(/,/g, '');
    },
    // Moeda "de calculadora": os dígitos digitados entram pelos centavos.
    // 1 -> 0,01 | 12 -> 0,12 | 123456 -> 1.234,56
    moeda: function (t) {
      var d = soDigitos(t).replace(/^0+(?=\d)/, '');
      if (!d) return '';
      while (d.length < 3) d = '0' + d;
      var inteiro = d.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
      return inteiro + ',' + d.slice(-2);
    }
  };

  function formatar(tipo, texto) {
    var f = formatadores[tipo];
    return f ? f(texto) : texto;
  }

  // '1.234,56' -> 123456 (centavos, inteiro). Útil para cálculos no navegador
  // SEM float; o valor oficial continua sendo calculado no backend.
  function moedaParaCentavos(texto) {
    var d = soDigitos(texto);
    return d ? parseInt(d, 10) : 0;
  }

  function aplicar(el) {
    var tipo = el.getAttribute('data-mask');
    if (!formatadores[tipo] || el.dataset.maskAplicada) return;
    el.dataset.maskAplicada = '1';
    // Teclado numérico no celular.
    el.setAttribute('inputmode', tipo === 'decimal' ? 'decimal' : 'numeric');
    el.addEventListener('input', function () {
      var novo = formatar(tipo, el.value);
      if (novo !== el.value) el.value = novo;
    });
    // Formata o valor que já vem preenchido (edição de registro).
    el.value = formatar(tipo, el.value);
  }

  function aplicarTodas(raiz) {
    (raiz || document).querySelectorAll('[data-mask]').forEach(aplicar);
  }

  document.addEventListener('DOMContentLoaded', function () { aplicarTodas(document); });

  global.SFCMascaras = { formatar: formatar, aplicar: aplicar, aplicarTodas: aplicarTodas, moedaParaCentavos: moedaParaCentavos };
})(window);
