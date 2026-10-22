from datetime import timedelta

from django.contrib import messages
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import FormView, ListView, TemplateView

from apps.auditoria.utils import registrar as registrar_auditoria
from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.produto.models import Produto
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import ListaPaginadaMixin, ListaPorUnidadeMixin
from web_project.regras import RegraNegocioError

from .etiquetas import EtiquetaForm, resposta_etiquetas
from .forms import ItensTransferenciaFormSet, MinimoForm, MovimentoEdicaoForm, MovimentoForm, MovimentoProdutoForm, TransferenciaForm
from .models import EstoqueMinimo, Lote, MovimentoEstoque, MovimentoProduto, TransferenciaEstoque
from .produto_estoque import TIPOS_MANUAIS_DE_ENTRADA, anotar_saldo_produto, registrar_baixa_manual_produto, registrar_entrada_produto
from .services import (
    alertas_de_estoque_baixo,
    alertas_de_vencimento,
    anotar_saldo,
    definir_minimo,
    dias_alerta_vencimento,
    MANTER,
    MANTER_VALIDADE,
    editar_movimento,
    registrar_entrada,
    registrar_saida,
    transferir,
)

CABECALHO_SALDOS = ["Insumo", "Unidade", "Medida", "Saldo", "Mínimo", "Situação", "Próximo vencimento"]
CABECALHO_MOVIMENTOS = ["Data", "Unidade", "Insumo", "Tipo", "Quantidade", "Lote", "Validade", "Saldo do lote após", "Responsável", "Observação"]


def _texto_filtros(request):
    return ", ".join(f"{k}={v}" for k, v in request.GET.items() if v and k not in ("export", "page", "per_page"))


def _qtd(valor):
    return f"{valor.normalize():f}".replace(".", ",")


def _situacao(insumo):
    if insumo.minimo > 0 and insumo.saldo < insumo.minimo:
        return "Baixo"
    return "Zerado" if insumo.saldo == 0 else "OK"


class EstoqueListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Saldos por insumo (separados por unidade: matriz e filiais), com mínimo,
    situação e próximo vencimento. Filtros, ordenação, paginação e exportação
    sobre o escopo de unidades do usuário. Alimentado pelos insumos cadastrados."""

    model = Insumo
    template_name = "estoque.html"
    context_object_name = "insumos"
    colunas_ordenaveis = {"nome": "nome", "saldo": "saldo", "vencimento": "proximo_vencimento"}

    def get_queryset(self):
        queryset = anotar_saldo(super().get_queryset().select_related("medida").filter(ativo=True))
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(nome__icontains=termo)
        situacao = params.get("situacao")
        if situacao == "baixo":
            queryset = queryset.filter(minimo__gt=0, saldo__lt=F("minimo"))
        elif situacao == "zerado":
            queryset = queryset.filter(saldo=0)
        elif situacao == "vencendo":
            queryset = queryset.filter(proximo_vencimento__lte=timezone.localdate() + timedelta(days=dias_alerta_vencimento()))
        return self.ordenar(queryset)

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(request, acao="exportar", modulo="estoque", descricao=f"Exportação de Saldos de estoque em {formato} ({queryset.count()} registro(s)).")
            linhas = [
                [i.nome, i.cliente.nome_razao_social, i.medida.sigla, _qtd(i.saldo), _qtd(i.minimo) if i.minimo else "—", _situacao(i),
                 i.proximo_vencimento.strftime("%d/%m/%Y") if i.proximo_vencimento else "—"]
                for i in queryset.select_related("cliente")
            ]
            if formato == "xlsx":
                return exportar_excel("estoque", "Estoque", CABECALHO_SALDOS, [[sanitizar_texto_excel(c) for c in l] for l in linhas])
            return exportar_pdf("estoque", "Relatório de Estoque", request.user, CABECALHO_SALDOS, linhas, _texto_filtros(request))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        escopo = self.clientes_do_escopo()
        hoje = timezone.localdate()
        for insumo in context["insumos"]:
            insumo.situacao = _situacao(insumo)
            insumo.vence_logo = bool(insumo.proximo_vencimento and insumo.proximo_vencimento <= hoje + timedelta(days=dias_alerta_vencimento()))
        # Atalho "Definir mínimo": só para quem pode alterar estoque e só nas unidades que pode alterar.
        pode_minimo = self.request.user.has_perm("estoque.change_movimentoestoque")
        alteraveis = set(clientes_dos_itens(self.request.user, "change").values_list("pk", flat=True)) if pode_minimo else set()
        for insumo in context["insumos"]:
            insumo.pode_definir_minimo = insumo.cliente_id in alteraveis
        context.update(
            situacao_selecionada=self.request.GET.get("situacao", ""),
            qtd_baixo=alertas_de_estoque_baixo(escopo).count(), qtd_vencendo=alertas_de_vencimento(escopo).count(),
            dias_vencimento=dias_alerta_vencimento(),
        )
        return context


class EstoqueInsumoView(TemplateView):
    """Um insumo: lotes (saldo e validade) e o histórico recente de movimentos."""

    template_name = "estoque_insumo.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        insumo = get_object_or_404(
            anotar_saldo(Insumo.objects.select_related("medida", "cliente")), pk=kwargs["pk"], cliente__in=clientes_dos_itens(self.request.user, "view")
        )
        context.update(
            insumo=insumo, lotes=Lote.objects.filter(insumo=insumo).order_by("validade", "criado_em"),
            movimentos=MovimentoEstoque.objects.filter(insumo=insumo).select_related("lote")[:30],
            pode_editar_minimo=insumo.cliente_id in clientes_dos_itens(self.request.user, "change").values_list("pk", flat=True),
        )
        return context


class MovimentarView(FormView):
    """Registra entrada, saída ou ajuste de um insumo da unidade do usuário."""

    form_class = MovimentoForm
    template_name = "estoque_movimentar.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["cliente"] = self.request.user.profile.cliente
        return kwargs

    def get_initial(self):
        inicial = super().get_initial()
        if self.request.GET.get("insumo", "").isdigit():
            inicial["insumo"] = self.request.GET["insumo"]
        if self.request.GET.get("tipo") in dict(MovimentoEstoque.TIPO_CHOICES):
            inicial["tipo"] = self.request.GET["tipo"]
        return inicial

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        dados = form.cleaned_data
        try:
            if dados["tipo"] in (MovimentoEstoque.ENTRADA, MovimentoEstoque.AJUSTE_ENTRADA):
                registrar_entrada(
                    self.request.user, dados["insumo"], dados["quantidade"], dados["medida"], dados["lote"], dados["validade"],
                    dados["observacao"], tipo=dados["tipo"],
                )
            else:
                registrar_saida(self.request.user, dados["insumo"], dados["quantidade"], dados["medida"], dados["observacao"], tipo=dados["tipo"], motivo=dados["motivo"])
        except RegraNegocioError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)
        messages.success(self.request, "Movimentação registrada.")
        return redirect("estoque-movimentar" if "salvar_e_novo" in self.request.POST else "estoque")


class MovimentoEditarView(FormView):
    """Corrige uma movimentação existente (quantidade, data, observação e, nas entradas,
    validade). Só movimentos de unidades que o usuário pode ALTERAR (escopo de tenant);
    o serviço recalcula os saldos, bloqueia saldo negativo e audita."""

    form_class = MovimentoEdicaoForm
    template_name = "estoque_movimento_editar.html"

    def get_movimento(self):
        return get_object_or_404(
            MovimentoEstoque.objects.select_related("insumo", "insumo__medida", "lote", "cliente"),
            pk=self.kwargs["pk"], cliente__in=clientes_dos_itens(self.request.user, "change"),
        )

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            movimento = self.get_movimento()
            if not movimento.editavel:
                messages.error(request, "Movimentações de transferência não podem ser editadas; registre uma nova transferência.")
                return redirect("estoque-movimentos")
        return super().dispatch(request, *args, **kwargs)

    def _entrada(self, movimento):
        return movimento.tipo in MovimentoEstoque.TIPOS_DE_ENTRADA

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["entrada"] = self._entrada(self.get_movimento())
        return kwargs

    def get_initial(self):
        movimento = self.get_movimento()
        return {
            "quantidade": movimento.quantidade.normalize(), "medida": movimento.insumo.medida_id,
            "data": timezone.localtime(movimento.data).strftime("%Y-%m-%dT%H:%M"),
            "validade": movimento.lote.validade, "observacao": movimento.observacao,
        }

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(movimento=self.get_movimento(), **kwargs))

    def form_valid(self, form):
        movimento = self.get_movimento()
        dados = form.cleaned_data
        try:
            editar_movimento(
                self.request, movimento.pk, clientes_dos_itens(self.request.user, "change"), dados["quantidade"], dados["medida"],
                dados["data"], dados["observacao"], dados.get("validade", MANTER_VALIDADE),
            )
        except RegraNegocioError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)
        messages.success(self.request, "Movimentação atualizada e saldos recalculados.")
        return redirect("estoque-movimentos")


class MinimoView(FormView):
    """Define a quantidade mínima de um insumo (0 = sem alerta)."""

    form_class = MinimoForm
    template_name = "estoque_minimo.html"

    def get_insumo(self):
        return get_object_or_404(
            Insumo.objects.select_related("medida", "cliente"), pk=self.kwargs["pk"], ativo=True, cliente__in=clientes_dos_itens(self.request.user, "change"),
        )

    def get_initial(self):
        atual = EstoqueMinimo.objects.filter(insumo=self.get_insumo()).first()
        return {
            "minimo": atual.minimo if atual else 0, "fator_reposicao": atual.fator_reposicao if atual else 2,
            "multiplo_compra": atual.multiplo_compra if atual else None,
        }

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(insumo=self.get_insumo(), **kwargs))

    def form_valid(self, form):
        insumo = self.get_insumo()
        try:
            definir_minimo(
                self.request, insumo, form.cleaned_data["minimo"], form.cleaned_data["fator_reposicao"] or MANTER, form.cleaned_data["multiplo_compra"],
            )
        except RegraNegocioError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)
        messages.success(self.request, f'Estoque mínimo de "{insumo.nome}" atualizado.')
        return redirect("estoque-insumo", pk=insumo.pk)


class MovimentosListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Histórico de movimentos (somente leitura), com filtros por período, tipo e
    insumo/lote/responsável, paginação e exportação, dentro do escopo de unidades."""

    model = MovimentoEstoque
    template_name = "estoque_movimentos.html"
    context_object_name = "movimentos"
    colunas_ordenaveis = {"data": "data", "insumo": "insumo__nome", "quantidade": "quantidade"}
    ordenacao_padrao = "data"
    direcao_padrao = "desc"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("insumo", "insumo__medida", "lote")
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(insumo__nome__icontains=termo) | Q(lote__codigo__icontains=termo) | Q(responsavel_nome__icontains=termo))
        if params.get("tipo") in dict(MovimentoEstoque.TIPO_CHOICES):
            queryset = queryset.filter(tipo=params["tipo"])
        de, ate = parse_date(params.get("data_de", "")), parse_date(params.get("data_ate", ""))
        if de:
            queryset = queryset.filter(data__date__gte=de)
        if ate:
            queryset = queryset.filter(data__date__lte=ate)
        return self.ordenar(queryset)

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(request, acao="exportar", modulo="estoque", descricao=f"Exportação de Movimentações de estoque em {formato} ({queryset.count()} registro(s)).")
            linhas = [
                [
                    timezone.localtime(m.data).strftime("%d/%m/%Y %H:%M"), m.cliente.nome_razao_social, m.insumo.nome, m.get_tipo_display(),
                    f"{'+' if m.sinal > 0 else '-'}{_qtd(m.quantidade)} {m.insumo.medida.sigla}", m.lote.codigo_exibicao or "—",
                    m.lote.validade.strftime("%d/%m/%Y") if m.lote.validade else "—", _qtd(m.saldo_lote_apos), m.responsavel_nome or "—", m.observacao,
                ]
                for m in queryset.select_related("cliente")
            ]
            if formato == "xlsx":
                # A quantidade (índice 4) é gerada pelo sistema e começa com "+"/"-": não é texto do usuário, então não leva o apóstrofo anti-fórmula.
                return exportar_excel(
                    "movimentacoes-estoque", "Movimentações", CABECALHO_MOVIMENTOS,
                    [[c if i == 4 else sanitizar_texto_excel(c) for i, c in enumerate(l)] for l in linhas],
                )
            return exportar_pdf("movimentacoes-estoque", "Relatório de Movimentações de estoque", request.user, CABECALHO_MOVIMENTOS, linhas, _texto_filtros(request))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            tipos=MovimentoEstoque.TIPO_CHOICES, tipo_selecionado=self.request.GET.get("tipo", ""),
            data_de=self.request.GET.get("data_de", ""), data_ate=self.request.GET.get("data_ate", ""),
        )
        return context


def _unidades_da_transferencia(usuario):
    """Origem = unidade do usuário; destinos = as OUTRAS unidades ativas da mesma
    estrutura (matriz ↔ filiais). Vazio para quem não tem cliente."""
    origem = usuario.profile.cliente
    if origem is None:
        return None, Cliente.objects.none()
    destinos = Cliente.objects.filter(Q(pk=origem.raiz_id) | Q(matriz_id=origem.raiz_id), ativo=True).exclude(pk=origem.pk).order_by("tipo_estabelecimento", "nome_razao_social")
    return origem, destinos


class TransferenciasListView(ListaPaginadaMixin, ListView):
    """Transferências em que a unidade do usuário (ou uma unidade que ele pode
    ver) é origem ou destino."""

    model = TransferenciaEstoque
    template_name = "estoque_transferencias.html"
    context_object_name = "transferencias"
    colunas_ordenaveis = {"data": "data"}
    ordenacao_padrao = "data"
    direcao_padrao = "desc"

    def get_queryset(self):
        visiveis = clientes_dos_itens(self.request.user, "view")
        propria = self.request.user.profile.cliente_id
        queryset = TransferenciaEstoque.objects.filter(Q(origem__in=visiveis) | Q(destino__in=visiveis) | Q(origem_id=propria) | Q(destino_id=propria))
        return self.ordenar(queryset.select_related("origem", "destino").distinct())

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class TransferenciaCreateView(FormView):
    form_class = TransferenciaForm
    template_name = "estoque_transferencia_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.origem, self.destinos = _unidades_da_transferencia(request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["destinos"] = self.destinos
        return kwargs

    def get_formset(self):
        insumos = Insumo.objects.filter(cliente=self.origem, ativo=True).select_related("medida").order_by("nome")
        return ItensTransferenciaFormSet(self.request.POST if self.request.method == "POST" else None, prefix="itens", insumos=insumos)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(formset=kwargs.get("formset") or self.get_formset(), origem=self.origem, sem_destinos=not self.destinos.exists())
        return context

    def post(self, request, *args, **kwargs):
        form, formset = self.get_form(), self.get_formset()
        if form.is_valid() and formset.is_valid():
            itens = [(f.cleaned_data["insumo"], f.cleaned_data["quantidade"], f.cleaned_data["medida"]) for f in formset.itens_validos()]
            try:
                transferencia = transferir(request.user, self.origem, form.cleaned_data["destino"], itens, form.cleaned_data["observacao"])
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, f"Transferência #{transferencia.pk} registrada.")
                return redirect("estoque-transferencia", pk=transferencia.pk)
        return self.render_to_response(self.get_context_data(form=form, formset=formset))


class TransferenciaDetalheView(TemplateView):
    template_name = "estoque_transferencia.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        visiveis = clientes_dos_itens(self.request.user, "view")
        propria = self.request.user.profile.cliente_id
        transferencia = get_object_or_404(
            TransferenciaEstoque.objects.select_related("origem", "destino").filter(
                Q(origem__in=visiveis) | Q(destino__in=visiveis) | Q(origem_id=propria) | Q(destino_id=propria)
            ).distinct(), pk=kwargs["pk"],
        )
        context.update(
            transferencia=transferencia,
            movimentos=transferencia.movimentos.select_related("insumo", "insumo__medida", "lote", "cliente").order_by("pk"),
        )
        return context


class AlertasView(TemplateView):
    """Estoque baixo e lotes vencidos/próximos do vencimento (escopo de unidades)."""

    template_name = "estoque_alertas.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        escopo = clientes_dos_itens(self.request.user, "view")
        hoje = timezone.localdate()
        lotes = list(alertas_de_vencimento(escopo))
        for lote in lotes:
            lote.vencido = lote.validade < hoje
            lote.dias = (lote.validade - hoje).days
        context.update(baixos=alertas_de_estoque_baixo(escopo), lotes=lotes, dias_vencimento=dias_alerta_vencimento())
        return context


class EtiquetaView(FormView):
    """Gera etiquetas (PDF) de um insumo: formato A4 ou térmica, lote opcional, validade digitada ou calculada."""

    template_name = "estoque_etiquetas.html"

    def get_form(self, form_class=None):
        insumos = Insumo.objects.filter(cliente__in=clientes_dos_itens(self.request.user, "view"), ativo=True).select_related("cliente").order_by("nome")
        dados = self.request.POST if self.request.method == "POST" else None
        inicial = {"emissao": timezone.localdate(), "modo_validade": "digitada", "copias": 1, "formato": "a4"}
        if self.request.GET.get("insumo", "").isdigit():
            inicial["insumo"] = self.request.GET["insumo"]
        return EtiquetaForm(dados, initial=inicial, insumos=insumos)

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        return resposta_etiquetas(request, form.cleaned_data["insumo"], form.cleaned_data)


# --------------------------------------------------------------------------
# Estoque de PRODUTOS (saldo próprio; regras em produto_estoque.py). Usa as mesmas permissões do estoque de insumos.
# --------------------------------------------------------------------------

class ProdutosEstoqueListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Saldos dos produtos que controlam estoque, dentro do escopo de unidades do usuário (matriz vê filiais como nos insumos)."""

    model = Produto
    template_name = "estoque_produtos.html"
    context_object_name = "produtos"
    colunas_ordenaveis = {"nome": "nome", "saldo": "saldo"}

    def get_queryset(self):
        queryset = anotar_saldo_produto(super().get_queryset().select_related("categoria", "cliente"))
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(nome__icontains=termo)
        if self.request.GET.get("situacao") == "zerado":
            queryset = queryset.filter(saldo=0)
        return self.ordenar(queryset)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["situacao_selecionada"] = self.request.GET.get("situacao", "")
        return context


class MovimentarProdutoView(FormView):
    """Registra entrada, baixa manual (com motivo) ou ajuste de um produto da unidade do usuário."""

    form_class = MovimentoProdutoForm
    template_name = "estoque_produto_movimentar.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["cliente"] = self.request.user.profile.cliente  # a movimentação é sempre da unidade do próprio usuário
        return kwargs

    def get_initial(self):
        inicial = super().get_initial()
        if self.request.GET.get("produto", "").isdigit():
            inicial["produto"] = self.request.GET["produto"]
        return inicial

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        dados = form.cleaned_data
        try:
            if dados["tipo"] in TIPOS_MANUAIS_DE_ENTRADA:
                registrar_entrada_produto(self.request.user, dados["produto"], dados["quantidade"], dados["observacao"], tipo=dados["tipo"])
            else:
                registrar_baixa_manual_produto(self.request.user, dados["produto"], dados["quantidade"], dados["motivo"], dados["observacao"], tipo=dados["tipo"])
        except RegraNegocioError as erro:
            form.add_error(None, str(erro))
            return self.form_invalid(form)
        messages.success(self.request, "Movimentação registrada.")
        return redirect("estoque-produto-movimentar" if "salvar_e_novo" in self.request.POST else "estoque-produtos")


class MovimentosProdutoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Histórico (somente leitura) do estoque dos produtos: filtros por texto, tipo, período e unidade."""

    model = MovimentoProduto
    template_name = "estoque_produto_movimentos.html"
    context_object_name = "movimentos"
    colunas_ordenaveis = {"data": "data", "produto": "produto__nome", "quantidade": "quantidade"}
    ordenacao_padrao = "data"
    direcao_padrao = "desc"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("produto", "cliente", "venda_item")
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(produto__nome__icontains=termo) | Q(responsavel_nome__icontains=termo) | Q(observacao__icontains=termo))
        if params.get("tipo") in dict(MovimentoProduto.TIPO_CHOICES):
            queryset = queryset.filter(tipo=params["tipo"])
        de, ate = parse_date(params.get("data_de", "")), parse_date(params.get("data_ate", ""))
        if de:
            queryset = queryset.filter(data__date__gte=de)
        if ate:
            queryset = queryset.filter(data__date__lte=ate)
        return self.ordenar(queryset)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            tipos=MovimentoProduto.TIPO_CHOICES, tipo_selecionado=self.request.GET.get("tipo", ""),
            data_de=self.request.GET.get("data_de", ""), data_ate=self.request.GET.get("data_ate", ""),
        )
        return context
