/**
 * Formulário de precificação: busca na lista de fichas (select2), mostra/oculta
 * campos conforme o canal e o método do lucro e exibe a prévia do cálculo.
 *
 * NENHUM cálculo é feito aqui: a cada alteração o navegador envia o formulário
 * ao servidor (POST em data-calculo-url) e só exibe a resposta — a mesma função
 * (`calcular_preco`) que grava o preço ao salvar. Se a prévia falhar, o último
 * resultado fica na tela e o servidor recalcula ao salvar.
 */
(function () {
  'use strict';
  var $ = window.jQuery;
  var form = document.getElementById('formPrecificacao');
  if (!form || !$) return;

  var url = form.dataset.calculoUrl;
  var aviso = document.getElementById('avisoPrevia');
  var estado = document.getElementById('estadoPrevia');
  var temporizador = null, ultima = 0;
  var campo = function (nome) { return form.querySelector('[name="' + nome + '"]'); };

  var AJUDA = {
    margem: 'Margem: % do PREÇO DE VENDA que sobra de lucro depois de custo e taxas.',
    markup: 'Markup: % acrescentada sobre o CUSTO (pode passar de 100%).',
    valor: ''
  };

  function mostrar(id, visivel) { document.getElementById(id).classList.toggle('d-none', !visivel); }

  function ajustarCampos() {
    var metodo = campo('metodo_lucro').value;
    mostrar('blocoPercentual', metodo !== 'valor');
    mostrar('blocoValor', metodo === 'valor');
    document.getElementById('ajudaPercentual').textContent = AJUDA[metodo] || '';
    document.getElementById('rotuloPercentual').textContent = metodo === 'markup' ? 'Markup (%)' : 'Margem (%)';
    mostrar('blocoPersonalizado', !campo('canal').value);  // canal cadastrado dispensa os campos personalizados
  }

  function limpar() {
    form.querySelectorAll('[data-res]').forEach(function (e) { e.textContent = '—'; });
  }

  function mostrarItens(d) {
    var corpo = document.querySelector('#tabelaItens tbody');
    if (!d.itens || !d.itens.length) {
      corpo.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">Selecione uma ficha técnica para ver os itens.</td></tr>';
    } else {
      corpo.innerHTML = '';
      d.itens.forEach(function (i) {
        var tr = document.createElement('tr');
        [i.insumo, i.quantidade_texto || (i.quantidade + ' ' + i.medida), i.custo].forEach(function (texto, idx) {
          var td = document.createElement('td');
          td.textContent = texto;  // textContent: nomes vindos do banco nunca viram HTML
          if (idx === 2) td.className = 'text-end';
          tr.appendChild(td);
        });
        corpo.appendChild(tr);
      });
    }
    // Mão de obra opcional da ficha: linhas e subtotais vindos do servidor (a soma fecha com o custo total).
    var moCorpo = document.getElementById('tabelaMaoDeObra');
    var temMo = !!(d.mao_de_obra && d.mao_de_obra.length);
    moCorpo.classList.toggle('d-none', !temMo);
    document.getElementById('linhaSubtotalMaoDeObra').classList.toggle('d-none', !temMo);
    moCorpo.querySelectorAll('tr:not(:first-child)').forEach(function (tr) { tr.remove(); });
    (d.mao_de_obra || []).forEach(function (m) {
      var tr = document.createElement('tr');
      [m.colaborador, m.horas + ' h', m.custo].forEach(function (texto, idx) {
        var td = document.createElement('td');
        td.textContent = texto;
        if (idx === 2) td.className = 'text-end';
        tr.appendChild(td);
      });
      moCorpo.appendChild(tr);
    });
    document.querySelector('[data-subtotal="insumos"]').textContent = d.subtotal_insumos || '—';
    document.querySelector('[data-subtotal="mao-de-obra"]').textContent = d.subtotal_mao_de_obra || '—';
    document.querySelectorAll('.total-ficha').forEach(function (e) { e.textContent = d.custo_ficha || '—'; });
  }

  // Custo operacional da categoria: o servidor devolve o valor da distribuição de custos e o campo é
  // preenchido sozinho. Se o usuário digitar outro valor, o campo passa a "manual" e deixa de ser
  // sobrescrito; digitar o valor automático (ou "Restaurar automático") volta ao modo automático.
  var origem = document.getElementById('origemCusto');
  var modoManual = origem.dataset.manual === '1';
  var sugestaoAtual = null;

  function numero(texto) { return parseFloat(String(texto || '').replace(/\./g, '').replace(',', '.')); }

  function atualizarOrigem() {
    var s = sugestaoAtual;
    var botao = document.getElementById('usarSugestao');
    document.getElementById('seloAutomatico').classList.toggle('d-none', !s || modoManual);
    document.getElementById('seloManual').classList.toggle('d-none', !s || !modoManual);
    botao.classList.toggle('d-none', !s || !modoManual);
    document.getElementById('textoSugestao').textContent = !s ? '' : (s.aviso
      ? ' Categoria ' + s.categoria + ': ' + s.aviso
      : ' Categoria ' + s.categoria + ': ' + s.formatado + ' por unidade' + (modoManual ? ' (valor automático)' : '') + '.');
    var resumo = document.getElementById('resumoCategoria');
    if (s && s.total_categoria) {
      resumo.textContent = 'Categoria ' + s.categoria + ': ' + s.total_categoria + ' no mês' + (s.ajuste_na_categoria ? ' (ajustado manualmente em Custo operacional)' : ' (calculado pelas vendas)') + '.';
      resumo.classList.remove('d-none');
    } else { resumo.classList.add('d-none'); }
  }

  function aplicarSugestao(s) {
    sugestaoAtual = s || null;
    if (s && !modoManual) {  // automático: acompanha a distribuição
      var campoCusto = campo('custo_operacional');
      if (campoCusto.value !== s.valor) { campoCusto.value = s.valor; agendar(); }  // refaz a prévia com o valor novo
    }
    atualizarOrigem();
  }

  $(campo('custo_operacional')).on('input', function (e) {
    if (e.isTrigger || !sugestaoAtual) return;  // só o que o usuário digita muda o modo
    modoManual = !(this.value.trim() !== '' && numero(this.value) === numero(sugestaoAtual.valor)) && this.value.trim() !== '';
    atualizarOrigem();
  });
  document.getElementById('usarSugestao').addEventListener('click', function () {
    modoManual = false;
    var campoCusto = campo('custo_operacional');
    if (sugestaoAtual) { campoCusto.value = sugestaoAtual.valor; }
    atualizarOrigem();
    $(campoCusto).trigger('input');  // dispara a prévia; isTrigger evita reclassificar como manual
  });

  function atualizar() {
    var id = ++ultima;
    estado.textContent = 'Calculando...';
    var dados = new FormData(form);
    fetch(url, { method: 'POST', body: dados, headers: { 'X-CSRFToken': campo('csrfmiddlewaretoken').value, 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (d) {
        if (id !== ultima) return;  // resposta antiga: descarta
        estado.textContent = '';
        mostrarItens(d);
        aplicarSugestao(d.sugestao_custo_operacional);
        var mensagens = Object.keys(d.erros || {}).map(function (k) { return d.erros[k]; });
        aviso.classList.toggle('d-none', !mensagens.length || !!d.resultado);
        aviso.textContent = mensagens.join(' ');
        if (!d.resultado) { limpar(); return; }
        Object.keys(d.resultado).forEach(function (k) {
          var el = form.querySelector('[data-res="' + k + '"]');
          if (el) el.textContent = d.resultado[k];
        });
      })
      .catch(function () { if (id === ultima) estado.textContent = 'Prévia indisponível (será calculado ao salvar).'; });
  }

  function agendar() { clearTimeout(temporizador); temporizador = setTimeout(atualizar, 350); }

  if ($.fn.select2) {
    $(campo('ficha')).select2({
      width: '100%', placeholder: 'Selecione...', dropdownParent: $(document.body),
      language: { noResults: function () { return 'Nenhuma ficha encontrada'; }, searching: function () { return 'Buscando...'; } }
    });
  }
  ajustarCampos();
  // Delegado em jQuery: recebe eventos nativos e o 'change' do select2.
  $(form).on('change input', '[data-prev]', function () { ajustarCampos(); agendar(); });
  atualizar();
})();
