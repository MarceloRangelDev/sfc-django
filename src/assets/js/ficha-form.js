/**
 * Formulário de ficha técnica: seletores de insumo com busca (select2) e
 * prévia do custo de cada linha e do total (topo e rodapé, sempre iguais).
 *
 * NENHUM cálculo é feito aqui: a cada alteração o navegador envia as linhas ao
 * servidor (POST em data-calculo-url) e só exibe o que ele devolve — o mesmo
 * cálculo que grava o custo ao salvar. Se a prévia falhar, o último valor
 * fica na tela e o servidor recalcula ao salvar.
 *
 * Marcação esperada: <form data-calculo-url="..." data-prefixo="...">; linhas em
 * `tbody#tabelaItens tr.linha-item` com campos terminados em -insumo, -medida
 * e -quantidade (e, para o metro, -dimensoes/-comprimento/-largura/-altura), uma `td.custo-item` por linha e elementos `.total-ficha`.
 * Ficha de serviço: opcionalmente `tbody#tabelaMaoDeObra tr.linha-mo` (campos -colaborador e
 * -horas, `td.custo-mo`), botão #adicionarMaoDeObra e <template id="modeloMaoDeObra">; o total
 * enviado/recebido soma insumos + mão de obra.
 */
(function () {
  'use strict';
  var $ = window.jQuery;
  // Serve à ficha técnica e ao processado: o formulário que traz o prefixo do formset e a URL da prévia.
  var form = document.querySelector('form[data-prefixo][data-calculo-url]');
  if (!form || !$) return;

  var corpo = form.querySelector('#tabelaItens tbody');
  var total = document.getElementById('id_' + form.dataset.prefixo + '-TOTAL_FORMS');
  var csrf = form.querySelector('input[name=csrfmiddlewaretoken]').value;
  var temporizador = null, ultima = 0;

  function iniciarBusca(contexto) {
    if (!$.fn.select2) return;
    $(contexto).find('select[name$="-insumo"]').each(function () {
      if ($(this).data('select2')) return;
      $(this).select2({
        width: '100%',
        placeholder: 'Selecione...',
        dropdownParent: $(document.body),
        language: {
          noResults: function () { return 'Nenhum insumo encontrado'; },
          searching: function () { return 'Buscando...'; }
        }
      });
    });
  }

  function linhas() { return Array.prototype.slice.call(corpo.querySelectorAll('tr.linha-item')); }

  function campo(linha, sufixo) { return linha.querySelector('[name$="' + sufixo + '"]'); }

  // Mão de obra (só na ficha de serviço): mesma ideia, tabela própria com o prefixo "mao_de_obra".
  var corpoMo = document.querySelector('#tabelaMaoDeObra tbody');
  var totalMo = document.getElementById('id_mao_de_obra-TOTAL_FORMS');
  function linhasMo() { return corpoMo ? Array.prototype.slice.call(corpoMo.querySelectorAll('tr.linha-mo')) : []; }

  function iniciarBuscaMo(contexto) {
    if (!$.fn.select2) return;
    $(contexto).find('select[name$="-colaborador"]').each(function () {
      if ($(this).data('select2')) return;
      $(this).select2({ width: '100%', placeholder: 'Selecione...', dropdownParent: $(document.body) });
    });
  }

  function mostrarTotal(texto) {
    document.querySelectorAll('.total-ficha').forEach(function (el) { el.textContent = texto; });
  }

  function atualizar() {
    var id = ++ultima;
    var rows = linhas();
    var dados = rows.map(function (l) {
      var remover = campo(l, '-DELETE');
      if (remover && remover.checked) return { insumo: '', medida: '', quantidade: '' };  // linha removida não conta
      return {
        insumo: campo(l, '-insumo').value,
        medida: campo(l, '-medida').value,
        quantidade: campo(l, '-quantidade').value,
        // metro em área/volume: o servidor calcula o produto das medidas (dimensoes.js só mostra/esconde os campos)
        dimensoes: campo(l, '-dimensoes').value,
        comprimento: campo(l, '-comprimento').value,
        largura: campo(l, '-largura').value,
        altura: campo(l, '-altura').value
      };
    });
    var rowsMo = linhasMo();
    var dadosMo = rowsMo.map(function (l) {
      var remover = campo(l, '-DELETE');
      if (remover && remover.checked) return { colaborador: '', horas: '' };
      return { colaborador: campo(l, '-colaborador').value, horas: campo(l, '-horas').value };
    });
    fetch(form.dataset.calculoUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      body: JSON.stringify({ itens: dados, mao_de_obra: dadosMo })
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (d) {
        if (id !== ultima) return;  // resposta antiga: descarta
        rows.forEach(function (l, i) {
          var celula = l.querySelector('td.custo-item');
          var item = d.itens[i] || {};
          celula.textContent = item.custo || '—';
          // insumo e quantidade informados, mas ainda sem unidade: orienta em vez de deixar o custo vazio
          if (celula.textContent === '—' && dados[i] && dados[i].insumo && dados[i].quantidade && !dados[i].medida) celula.textContent = 'Escolha a unidade';
          celula.title = item.erro || '';
          celula.classList.toggle('text-danger', !!item.erro);
          // quantidade por extenso (ex.: "2 unidades") logo abaixo do campo, assim que há quantidade e unidade
          var extenso = l.querySelector('.extenso-item');
          if (extenso) extenso.textContent = item.extenso || '';
        });
        rowsMo.forEach(function (l, i) {
          var celula = l.querySelector('td.custo-mo');
          var linha = (d.mao_de_obra || [])[i] || {};
          celula.textContent = linha.custo || '—';
          celula.title = linha.erro || '';
          celula.classList.toggle('text-danger', !!linha.erro);
        });
        mostrarTotal(d.total);
      })
      .catch(function () { /* mantém os valores atuais */ });
  }

  function agendar() { clearTimeout(temporizador); temporizador = setTimeout(atualizar, 300); }

  iniciarBusca(form);
  iniciarBuscaMo(form);
  agendar();  // formulário de edição já vem preenchido: mostra custos e extenso sem esperar uma digitação
  // Delegado em jQuery: recebe tanto eventos nativos quanto o 'change' do select2.
  $(form).on('change input', '#tabelaItens select, #tabelaItens input, #tabelaMaoDeObra select, #tabelaMaoDeObra input', agendar);

  document.getElementById('adicionarLinha').addEventListener('click', function () {
    var html = document.getElementById('modeloLinha').innerHTML.replace(/__prefix__/g, total.value);
    corpo.insertAdjacentHTML('beforeend', html);
    total.value = parseInt(total.value, 10) + 1;
    iniciarBusca(corpo.lastElementChild);
  });

  var botaoMo = document.getElementById('adicionarMaoDeObra');
  if (botaoMo && corpoMo && totalMo) {
    botaoMo.addEventListener('click', function () {
      var html = document.getElementById('modeloMaoDeObra').innerHTML.replace(/__prefix__/g, totalMo.value);
      corpoMo.insertAdjacentHTML('beforeend', html);
      totalMo.value = parseInt(totalMo.value, 10) + 1;
      iniciarBuscaMo(corpoMo.lastElementChild);
    });
    corpoMo.addEventListener('click', function (e) {
      var botao = e.target.closest('.remover-linha-mo');
      if (botao) { botao.closest('tr').remove(); agendar(); }
    });
  }

  corpo.addEventListener('click', function (e) {
    var botao = e.target.closest('.remover-linha');
    if (botao) { botao.closest('tr').remove(); agendar(); }
  });
  // Linha existente marcada para remover: sai do total.
  $(form).on('change', 'input[name$="-DELETE"]', function () {
    $(this).closest('tr').toggleClass('linha-removida', this.checked).toggleClass('text-decoration-line-through', this.checked);
  });
})();
