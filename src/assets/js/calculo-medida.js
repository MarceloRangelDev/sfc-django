/**
 * Prévia de "quantidade por extenso" e "PREÇO (por KG/L/UN)" em qualquer
 * formulário que tenha uma medida, uma quantidade e (opcional) um valor.
 *
 * Componente único e reutilizável — não tem nada específico de insumos.
 * Os cálculos NÃO são feitos aqui: o navegador pergunta ao backend
 * (GET /medidas/calcular), que usa as mesmas funções de
 * web_project/regras/. Assim a prévia nunca diverge do valor gravado.
 *
 * Uso no HTML:
 *   <form data-calculo-url="{% url 'medida-calcular' %}">
 *     <select data-calc="medida">…</select>
 *     <input  data-calc="quantidade">
 *     <input  data-calc="preco">                      (opcional)
 *     <div data-calc-saida="extenso"></div>           (ex.: "100 gramas")
 *     <label data-calc-saida="rotulo"></label>        (ex.: "PREÇO (por KG)")
 *     <input data-calc-saida="preco" readonly>        (ex.: "R$ 40,00")
 *   </form>
 */
(function () {
  'use strict';

  function iniciar(form) {
    var url = form.getAttribute('data-calculo-url');
    var campo = function (nome) { return form.querySelector('[data-calc="' + nome + '"]'); };
    var saida = function (nome) { return form.querySelector('[data-calc-saida="' + nome + '"]'); };
    var medida = campo('medida'), quantidade = campo('quantidade'), preco = campo('preco');
    if (!url || !medida || !quantidade) return;

    var temporizador = null, ultimaRequisicao = 0;

    function escrever(nome, texto) {
      var el = saida(nome);
      if (!el) return;
      if ('value' in el) el.value = texto || ''; else el.textContent = texto || '';
    }

    function atualizar() {
      var numero = ++ultimaRequisicao; // ignora respostas antigas (usuário digitando rápido)
      var params = new URLSearchParams({
        medida: medida.value, quantidade: quantidade.value, preco: preco ? preco.value : ''
      });
      fetch(url + '?' + params.toString(), { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (dados) {
          if (numero !== ultimaRequisicao) return;
          escrever('extenso', dados.quantidade_extenso);
          escrever('rotulo', dados.rotulo_preco);
          escrever('preco', dados.preco_unidade ? dados.preco_unidade.formatado : '');
        })
        .catch(function () { /* prévia é opcional: falha de rede não atrapalha o cadastro */ });
    }

    function agendar() {
      clearTimeout(temporizador);
      temporizador = setTimeout(atualizar, 200);
    }

    [medida, quantidade, preco].forEach(function (el) {
      if (el) { el.addEventListener('input', agendar); el.addEventListener('change', agendar); }
    });
    atualizar(); // formulário de edição já vem preenchido
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-calculo-url]').forEach(iniciar);
  });
})();
