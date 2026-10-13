from django.contrib import messages
from django.db.models import F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar as registrar_auditoria
from apps.auditoria.utils import registrar_criacao
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import (
    ClienteScopedMixin,
    ExcluirOuDesativarView,
    ListaPaginadaMixin,
    ListaPorUnidadeMixin,
    SalvarEAdicionarMixin,
)
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .forms import BaixaForm, CategoriaFinanceiraForm, PeriodoForm, conta_form_para
from .models import (
    STATUS_CANCELADA,
    TIPO_DESPESA,
    TIPO_RECEITA,
    BaixaBase,
    CategoriaFinanceira,
)
from .services import (
    CONFIG,
    balanco,
    conta_em_atraso_ou_a_vencer,
    dias_alerta_vencimento,
    dre,
    estornar_baixa,
    registrar_baixa,
)

STATUS_FILTROS = [
    ("aberta", "Em aberto"), ("parcial", "Parcialmente paga/recebida"), ("paga", "Paga/recebida"),
    ("atrasada", "Atrasada"), ("cancelada", "Cancelada"),
]
CABECALHO_EXPORTACAO = ["Descrição", "Fornecedor/Cliente", "Categoria", "Unidade", "Emissão", "Vencimento", "Valor", "Baixado", "Saldo", "Situação"]
PADRAO_CATEGORIAS = {
    TIPO_RECEITA: ["Vendas", "Outras receitas"],
    TIPO_DESPESA: ["Fornecedores", "Aluguel e condomínio", "Energia, água e internet", "Salários e encargos", "Impostos e taxas", "Outras despesas"],
}


def _data(valor):
    return valor.strftime("%d/%m/%Y")


def _situacao_texto(conta):
    base = {"aberta": "Em aberto", "parcial": "Parcial", "paga": "Paga", "cancelada": "Cancelada"}[conta.status]
    return f"{base} (atrasada)" if conta.atrasada else base


def _pode(usuario, conta, acao, modelo_nome):
    """Permissão do módulo na ação + escopo: a própria unidade ou, para a matriz, as filiais com `<acao>_itensfilial`."""
    if not usuario.has_perm(f"financeiro.{acao}_{modelo_nome}"):
        return False
    return conta.cliente_id == usuario.profile.cliente_id or usuario.has_perm(f"cliente.{acao}_itensfilial")


# --------------------------------------------------------------------------
# Contas a pagar / a receber (classes parametrizadas por `chave`)
# --------------------------------------------------------------------------

class ContaMixin:
    """Dados do tipo de conta (pagar/receber) para as views: `chave` ∈ CONFIG."""

    chave = None

    @property
    def modelo(self):
        return CONFIG[self.chave][0]

    @property
    def modelo_nome(self):
        return self.modelo._meta.model_name

    def nomes(self):
        return {n: f"conta-{self.chave}-{n}" for n in ("list", "create", "detail", "edit", "baixar", "estornar", "excluir")}

    def contexto_basico(self):
        pagar = self.chave == "pagar"
        return {
            "chave": self.chave, "nomes": self.nomes(), "titulo": "Contas a pagar" if pagar else "Contas a receber",
            "rotulo_conta": "conta a pagar" if pagar else "conta a receber", "rotulo_contraparte": "Fornecedor" if pagar else "Cliente",
            "verbo_baixa": "Pagar" if pagar else "Receber", "rotulo_baixado": "Pago" if pagar else "Recebido",
            "pode_add": self.request.user.has_perm(f"financeiro.add_{self.modelo_nome}"),
        }


class ContaListView(ContaMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Contas com filtros (texto, categoria, situação, período de vencimento, unidade),
    ordenação, paginação, totais do filtro e exportação Excel/PDF, sobre o escopo de unidades."""

    template_name = "conta_list.html"
    context_object_name = "contas"
    colunas_ordenaveis = {
        "vencimento": "vencimento", "descricao": "descricao", "contraparte": "contraparte", "categoria": "categoria__nome", "valor": "valor",
    }
    ordenacao_padrao = "vencimento"

    @property
    def model(self):
        return self.modelo

    def get_queryset(self):
        queryset = super().get_queryset().select_related("categoria")
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(descricao__icontains=termo) | Q(contraparte__icontains=termo) | Q(categoria__nome__icontains=termo))
        if params.get("categoria", "").isdigit():
            queryset = queryset.filter(categoria_id=params["categoria"])
        situacao, hoje = params.get("situacao", ""), timezone.localdate()
        if situacao == "cancelada":
            queryset = queryset.filter(ativo=False)
        else:
            queryset = queryset.filter(ativo=True)
            if situacao == "aberta":
                queryset = queryset.filter(valor_baixado=0)
            elif situacao == "parcial":
                queryset = queryset.filter(valor_baixado__gt=0, valor_baixado__lt=F("valor"))
            elif situacao == "paga":
                queryset = queryset.filter(valor_baixado=F("valor"))
            elif situacao == "atrasada":
                queryset = queryset.filter(valor_baixado__lt=F("valor"), vencimento__lt=hoje)
        de, ate = parse_date(params.get("vencimento_de", "")), parse_date(params.get("vencimento_ate", ""))
        if de:
            queryset = queryset.filter(vencimento__gte=de)
        if ate:
            queryset = queryset.filter(vencimento__lte=ate)
        return self.ordenar(queryset)

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(request, acao="exportar", modulo="financeiro", descricao=f"Exportação de {self.contexto_basico()['titulo']} em {formato} ({queryset.count()} registro(s)).")
            linhas = [
                [
                    c.descricao, c.contraparte or "—", c.categoria.nome, c.cliente.nome_razao_social, _data(c.emissao), _data(c.vencimento),
                    c.valor_formatado, c.baixado_formatado, c.saldo_formatado, _situacao_texto(c),
                ]
                for c in queryset.select_related("cliente")
            ]
            arquivo = "contas-a-pagar" if self.chave == "pagar" else "contas-a-receber"
            if formato == "xlsx":
                return exportar_excel(arquivo, self.contexto_basico()["titulo"], CABECALHO_EXPORTACAO, [[sanitizar_texto_excel(c) for c in l] for l in linhas])
            filtros = ", ".join(f"{k}={v}" for k, v in request.GET.items() if v and k not in ("export", "page", "per_page"))
            return exportar_pdf(arquivo, f"Relatório de {self.contexto_basico()['titulo']}", request.user, CABECALHO_EXPORTACAO, linhas, filtros)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        escopo = self.clientes_do_escopo()
        totais = self.get_queryset().aggregate(valor=Sum("valor"), baixado=Sum("valor_baixado"))
        valor, baixado = totais["valor"] or 0, totais["baixado"] or 0
        for conta in context["contas"]:
            conta.pode_editar = _pode(self.request.user, conta, "change", self.modelo_nome)
            conta.pode_excluir = _pode(self.request.user, conta, "delete", self.modelo_nome)
            conta.situacao = _situacao_texto(conta)
        atrasadas, vencendo = conta_em_atraso_ou_a_vencer(self.modelo, escopo)
        categorias = CategoriaFinanceira.objects.filter(cliente__in=escopo, tipo=CONFIG[self.chave][3]).order_by("nome")
        context.update(
            self.contexto_basico(), categorias=categorias, situacoes=STATUS_FILTROS,
            categoria_selecionada=self.request.GET.get("categoria", ""), situacao_selecionada=self.request.GET.get("situacao", ""),
            vencimento_de=self.request.GET.get("vencimento_de", ""), vencimento_ate=self.request.GET.get("vencimento_ate", ""),
            total_valor=DinheiroService.formatar(valor), total_baixado=DinheiroService.formatar(baixado), total_saldo=DinheiroService.formatar(valor - baixado),
            qtd_atrasadas=atrasadas, qtd_vencendo=vencendo, dias_alerta=dias_alerta_vencimento(),
        )
        return context


class ContaFormMixin(ContaMixin):
    template_name = "conta_form.html"

    @property
    def form_class(self):
        return conta_form_para(self.modelo, CONFIG[self.chave][3])

    @property
    def success_url(self):
        return reverse_lazy(self.nomes()["list"])

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, {**super().get_context_data(**kwargs), **self.contexto_basico()})


class ContaCreateView(ContaFormMixin, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"

    @property
    def model(self):
        return self.modelo

    @property
    def nome_url_novo(self):
        return self.nomes()["create"]

    def form_valid(self, form):
        messages.success(self.request, "Conta cadastrada com sucesso.")
        return super().form_valid(form)


class ContaUpdateView(ContaFormMixin, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    @property
    def model(self):
        return self.modelo

    def get_queryset(self):
        return super().get_queryset().filter(ativo=True)  # conta cancelada não se edita

    def form_valid(self, form):
        messages.success(self.request, "Conta atualizada com sucesso.")
        return super().form_valid(form)


class ContaDetalheView(ContaMixin, TemplateView):
    """Uma conta com o histórico de baixas/estornos e o formulário de baixa."""

    template_name = "conta_detalhe.html"

    def get_conta(self):
        return get_object_or_404(
            self.modelo.objects.select_related("categoria", "cliente"), pk=self.kwargs["pk"], cliente__in=clientes_dos_itens(self.request.user, "view")
        )

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        conta = self.get_conta()
        baixas = list(conta.baixas.select_related("estornada_por"))
        pode_baixar = conta.ativo and conta.saldo > 0 and _pode(self.request.user, conta, "change", self.modelo_nome)
        for baixa in baixas:
            baixa.estornada = hasattr(baixa, "estornada_por")
            baixa.pode_estornar = baixa.tipo == BaixaBase.BAIXA and not baixa.estornada and conta.ativo and _pode(self.request.user, conta, "change", self.modelo_nome)
        context.update(
            self.contexto_basico(), conta=conta, baixas=baixas, pode_baixar=pode_baixar, situacao=_situacao_texto(conta),
            form_baixa=BaixaForm(),
            pode_editar=conta.ativo and _pode(self.request.user, conta, "change", self.modelo_nome),
        )
        return context


class ContaBaixarView(ContaMixin, View):
    """POST: registra a baixa (pagamento/recebimento, total ou parcial)."""

    def post(self, request, pk):
        conta = get_object_or_404(self.modelo, pk=pk, cliente__in=clientes_dos_itens(request.user, "change"))
        form = BaixaForm(request.POST)
        if form.is_valid():
            try:
                registrar_baixa(request.user, conta, form.cleaned_data["valor"], form.cleaned_data["data"], form.cleaned_data["observacao"])
            except RegraNegocioError as erro:
                messages.error(request, str(erro))
            else:
                messages.success(request, "Baixa registrada.")
        else:
            messages.error(request, " ".join(m for erros in form.errors.values() for m in erros))
        return redirect(self.nomes()["detail"], pk=conta.pk)


class ContaEstornarView(ContaMixin, View):
    """POST: estorna uma baixa (cria um lançamento de estorno; a baixa fica no histórico)."""

    def post(self, request, pk, baixa_pk):
        conta = get_object_or_404(self.modelo, pk=pk, cliente__in=clientes_dos_itens(request.user, "change"))
        baixa = get_object_or_404(conta.baixas.all(), pk=baixa_pk)
        try:
            estornar_baixa(request.user, baixa, request.POST.get("observacao", "").strip())
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Baixa estornada.")
        return redirect(self.nomes()["detail"], pk=conta.pk)


class ContaExcluirView(ContaMixin, ExcluirOuDesativarView):
    """Conta sem baixas é apagada; com baixas, só pode ser CANCELADA depois de
    estornar todas (senão o dinheiro já movimentado ficaria sem conta ativa)."""

    @property
    def model(self):
        return self.modelo

    @property
    def url_lista(self):
        return self.nomes()["list"]

    rotulo = "conta"
    artigo = "a"

    def post(self, request, pk):
        conta = get_object_or_404(self.modelo, pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"))
        if conta.valor_baixado > 0:
            messages.error(request, "A conta tem baixas registradas. Estorne as baixas antes de excluí-la ou cancelá-la.")
            return redirect(self.nomes()["detail"], pk=conta.pk)
        return super().post(request, pk)


def criar_views(chave):
    """As views de um tipo de conta (`pagar` ou `receber`), prontas para as URLs."""
    atributos = {"chave": chave}
    return {
        "list": type(f"Conta{chave.title()}ListView", (ContaListView,), atributos),
        "create": type(f"Conta{chave.title()}CreateView", (ContaCreateView,), atributos),
        "edit": type(f"Conta{chave.title()}UpdateView", (ContaUpdateView,), atributos),
        "detail": type(f"Conta{chave.title()}DetalheView", (ContaDetalheView,), atributos),
        "baixar": type(f"Conta{chave.title()}BaixarView", (ContaBaixarView,), atributos),
        "estornar": type(f"Conta{chave.title()}EstornarView", (ContaEstornarView,), atributos),
        "excluir": type(f"Conta{chave.title()}ExcluirView", (ContaExcluirView,), atributos),
    }


# --------------------------------------------------------------------------
# Categorias financeiras
# --------------------------------------------------------------------------

class CategoriasView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Lista e cadastro de categorias da unidade do usuário (o POST cria)."""

    model = CategoriaFinanceira
    template_name = "financeiro_categorias.html"
    context_object_name = "categorias"
    colunas_ordenaveis = {"nome": "nome", "tipo": "tipo"}
    status_padrao = "todos"

    def get_queryset(self):
        return self.ordenar(self.filtrar_status(super().get_queryset()))

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            form=kwargs.get("form") or CategoriaFinanceiraForm(cliente=self.request.user.profile.cliente),
            pode_add=self.request.user.has_perm("financeiro.add_categoriafinanceira"),
            pode_change=self.request.user.has_perm("financeiro.change_categoriafinanceira"),
            pode_delete=self.request.user.has_perm("financeiro.delete_categoriafinanceira"),
        )
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("financeiro.add_categoriafinanceira"):
            raise Http404
        cliente = request.user.profile.cliente
        form = CategoriaFinanceiraForm(request.POST, cliente=cliente)
        if form.is_valid():
            categoria = form.save(commit=False)
            categoria.cliente = cliente
            categoria.save()
            registrar_criacao(request, categoria)
            messages.success(request, "Categoria cadastrada.")
            return redirect("financeiro-categorias")
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(form=form))


class CategoriaPadraoView(View):
    """POST: cria, na unidade do usuário, as categorias padrão que ainda não existem
    (nada é copiado automaticamente: só quando o usuário pede)."""

    def post(self, request):
        cliente = request.user.profile.cliente
        criadas = 0
        for tipo, nomes in PADRAO_CATEGORIAS.items():
            for nome in nomes:
                categoria, nova = CategoriaFinanceira.objects.get_or_create(cliente=cliente, tipo=tipo, nome=nome)
                if nova:
                    registrar_criacao(request, categoria)
                    criadas += 1
        messages.success(request, f"{criadas} categoria(s) padrão criada(s)." if criadas else "As categorias padrão já existem.")
        return redirect("financeiro-categorias")


class CategoriaAlternarAtivoView(View):
    def post(self, request, pk):
        from apps.auditoria.utils import registrar_edicao, tirar_foto

        categoria = get_object_or_404(CategoriaFinanceira, pk=pk, cliente__in=clientes_dos_itens(request.user, "change"))
        foto = tirar_foto(categoria)
        categoria.ativo = not categoria.ativo
        categoria.save(update_fields=["ativo", "atualizado_em"])
        registrar_edicao(request, categoria, foto)
        messages.success(request, f'Categoria "{categoria.nome}" {"ativada" if categoria.ativo else "desativada"}.')
        return redirect("financeiro-categorias")


class CategoriaExcluirView(ExcluirOuDesativarView):
    """Sem contas -> apagada; com contas -> só desativada."""

    model = CategoriaFinanceira
    url_lista = "financeiro-categorias"
    rotulo = "categoria"
    artigo = "a"


# --------------------------------------------------------------------------
# Relatórios gerenciais: DRE e balanço
# --------------------------------------------------------------------------

class RelatorioMixin:
    """Unidade do relatório: a do usuário; a matriz pode escolher uma filial se
    tiver `cliente.view_itensfilial`. Um relatório nunca mistura unidades."""

    def unidade(self):
        unidades = list(clientes_dos_itens(self.request.user, "view").order_by("tipo_estabelecimento", "nome_razao_social"))
        escolhida = self.request.GET.get("unidade", "")
        return unidades, next((u for u in unidades if str(u.pk) == escolhida), None) or self.request.user.profile.cliente

    def base(self, context):
        unidades, unidade = self.unidade()
        context.update(unidades=unidades, mostrar_unidade=len(unidades) > 1, unidade_atual=unidade, unidade_selecionada=str(unidade.pk))
        return unidade


class DreView(RelatorioMixin, TemplateView):
    template_name = "financeiro_dre.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        unidade = self.base(context)
        form = PeriodoForm(self.request.GET or None)
        inicio, fim, regime = form.periodo()
        try:
            relatorio = dre(unidade, inicio, fim, regime)
        except RegraNegocioError as erro:
            messages.error(self.request, str(erro))
            relatorio = dre(unidade, *PeriodoForm().periodo())
            inicio, fim = relatorio["inicio"], relatorio["fim"]
        formatar = DinheiroService.formatar
        context.update(
            relatorio=relatorio, inicio=inicio, fim=fim, regime=relatorio["regime"],
            receitas=[(n, formatar(t)) for n, t in relatorio["receitas"]], despesas=[(n, formatar(t)) for n, t in relatorio["despesas"]],
            total_receitas=formatar(relatorio["total_receitas"]), total_despesas=formatar(relatorio["total_despesas"]),
            resultado=formatar(relatorio["resultado"]), resultado_negativo=relatorio["resultado"] < 0,
        )
        return context


class BalancoView(RelatorioMixin, TemplateView):
    template_name = "financeiro_balanco.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        unidade = self.base(context)
        data = parse_date(self.request.GET.get("data", "")) or timezone.localdate()
        quadro = balanco(unidade, data)
        formatar = DinheiroService.formatar
        context.update(
            data=data, patrimonio_negativo=quadro["patrimonio"] < 0,
            **{chave: formatar(valor) for chave, valor in quadro.items() if chave != "data"},
        )
        return context
