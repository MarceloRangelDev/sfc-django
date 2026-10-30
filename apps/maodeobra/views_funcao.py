"""Telas de funções de mão de obra, composição do colaborador, simulações de promoção e dashboard.

Toda consulta é restrita ao escopo de unidades do usuário (web_project/acesso.clientes_dos_itens); itens-filho (itens de custo,
vínculos, personalizações) são alcançados SEMPRE pelo pai já filtrado, nunca por id solto. Os valores são informativos:
nada aqui altera apontamentos, fichas ou o valor da hora do colaborador (ver composicao.py)."""
import re
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_exclusao
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ExcluirOuDesativarView, SalvarEAdicionarMixin

from . import composicao, custos
from .forms_funcao import ComponenteForm, DashboardForm, FuncaoForm, PersonalizacaoForm, SimulacaoForm
from .models import Colaborador, ComponenteFuncao, FuncaoMaoDeObra, SimulacaoPromocao
from .views import SegmentoMixin, _Form, _Lista

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def data_da_consulta(request, nome="data", padrao=None):
    """Data ISO (AAAA-MM-DD) da querystring; inválida ou ausente = `padrao` (hoje). Nunca levanta erro."""
    texto = request.GET.get(nome, "")
    if _ISO.match(texto):
        try:
            return date.fromisoformat(texto)
        except ValueError:
            pass
    return padrao or timezone.localdate()


# --------------------------------------------------------------------------
# Funções
# --------------------------------------------------------------------------

class FuncaoListView(_Lista):
    model = FuncaoMaoDeObra
    template_name = "funcao.html"
    context_object_name = "funcoes"
    colunas_ordenaveis = {"nome": "nome", "cbo": "cbo_codigo"}

    def get_queryset(self):
        queryset = super().get_queryset().annotate(qtd_colaboradores=Count("vinculos", filter=Q(vinculos__fim__isnull=True), distinct=True))
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(cbo_codigo__icontains=termo) | Q(cbo_descricao__icontains=termo))
        return self.ordenar(self.filtrar_status(queryset))


class FuncaoCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = FuncaoMaoDeObra, FuncaoForm, "funcao_form.html"
    success_url = reverse_lazy("funcao-list")
    acao_escopo, nome_url_novo = "add", "funcao-create"

    def form_valid(self, form):
        messages.success(self.request, "Função cadastrada. Agora cadastre o salário base e os demais itens de custo.")
        return super().form_valid(form)

    def get_success_url(self):
        if "salvar_e_novo" in self.request.POST:  # mantém o fluxo "salvar e adicionar outro" do mixin
            return super().get_success_url()
        return reverse("funcao-detalhe", args=[self.object.pk])


class FuncaoUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = FuncaoMaoDeObra, FuncaoForm, "funcao_form.html"
    success_url = reverse_lazy("funcao-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Função atualizada.")
        return super().form_valid(form)


class FuncaoExcluirView(SegmentoMixin, ExcluirOuDesativarView):
    """Função sem colaboradores vinculados (nem histórico) é apagada com seus itens; com vínculo, só desativada."""

    model, url_lista, rotulo, artigo = FuncaoMaoDeObra, "funcao-list", "função", "a"


class FuncaoDetalheView(SegmentoMixin, ClienteScopedMixin, DetailView):
    """Itens de custo da função (por categoria e vigência) e a composição de referência na data escolhida."""

    model = FuncaoMaoDeObra
    template_name = "funcao_detalhe.html"
    context_object_name = "funcao"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        data = data_da_consulta(self.request)
        funcao = self.object
        itens = list(funcao.componentes.order_by("categoria", "-inicio", "nome"))
        ordem = {codigo: i for i, codigo in enumerate(custos.ORDEM)}
        itens.sort(key=lambda c: (ordem[c.categoria], c.inicio, c.nome))
        referencia = composicao.composicao_da_funcao(funcao, data)
        context.update(
            itens=itens, data=data, referencia=referencia, periodos=custos.PERIODOS, hoje=timezone.localdate(),
            categorias=[(codigo, rotulo, referencia.composicao.por_categoria[codigo]) for codigo, rotulo in custos.CATEGORIAS],
            colaboradores=Colaborador.objects.filter(vinculos_funcao__funcao=funcao, vinculos_funcao__fim__isnull=True).order_by("nome"),
        )
        return context


class _ComponenteBase(SegmentoMixin):
    """Item de custo: o escopo é o da FUNÇÃO-pai (o item não tem coluna de unidade)."""

    model, form_class, template_name = ComponenteFuncao, ComponenteForm, "componente_form.html"
    acao_escopo = "change"  # mexer em itens de custo é alterar a função

    def funcao_alvo(self):
        raise NotImplementedError

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(funcao=self.funcao_alvo(), cliente=self.funcao_alvo().cliente, unidades=None)
        return kwargs

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["funcao"] = self.funcao_alvo()
        context["bases_por_categoria"] = {k: list(v) for k, v in custos.BASES_POR_CATEGORIA.items()}
        return context

    def get_success_url(self):
        return reverse("funcao-detalhe", args=[self.funcao_alvo().pk])


class ComponenteCreateView(_ComponenteBase, AuditoriaFormMixin, CreateView):
    def funcao_alvo(self):
        if not hasattr(self, "_funcao"):
            self._funcao = get_object_or_404(FuncaoMaoDeObra, pk=self.kwargs["pk"], cliente__in=clientes_dos_itens(self.request.user, "change"))
        return self._funcao

    def form_valid(self, form):
        form.instance.funcao = self.funcao_alvo()
        messages.success(self.request, "Item de custo cadastrado.")
        return super().form_valid(form)


class ComponenteUpdateView(_ComponenteBase, AuditoriaFormMixin, UpdateView):
    def get_queryset(self):
        return ComponenteFuncao.objects.select_related("funcao").filter(funcao__cliente__in=clientes_dos_itens(self.request.user, "change"))

    def funcao_alvo(self):
        return self.object.funcao  # self.object já foi carregado (e fotografado para a auditoria) pela UpdateView

    def form_valid(self, form):
        messages.success(self.request, "Item de custo atualizado. Colaboradores já calculados são recalculados na próxima consulta.")
        return super().form_valid(form)


class ComponenteExcluirView(SegmentoMixin, View):
    """Item que ainda NÃO entrou em vigor e sem personalização é apagado; os demais são ENCERRADOS (fim = ontem), para o passado continuar calculável."""

    def post(self, request, pk):
        with transaction.atomic():
            item = get_object_or_404(ComponenteFuncao.objects.select_for_update().select_related("funcao"), pk=pk, funcao__cliente__in=clientes_dos_itens(request.user, "delete"))
            if composicao.encerrar_ou_excluir_componente(request, item) == "excluido":
                messages.success(request, "Item de custo excluído.")
            else:
                messages.warning(request, "O item já vigorou e é histórico: foi encerrado, não apagado.")
        return redirect("funcao-detalhe", pk=item.funcao_id)


# --------------------------------------------------------------------------
# Composição do colaborador (função, herdado × personalizado, períodos)
# --------------------------------------------------------------------------

class ColaboradorComposicaoView(SegmentoMixin, View):
    def _colaborador(self, request, pk, acao):
        return get_object_or_404(Colaborador.objects.select_related("cliente"), pk=pk, cliente__in=clientes_dos_itens(request.user, acao))

    def _contexto(self, request, colaborador, form=None):
        data = data_da_consulta(request)
        resultado = composicao.composicao_do_colaborador(colaborador, data)
        if form is None and resultado.vinculo:
            form = PersonalizacaoForm(vinculo=resultado.vinculo, componentes=self._componentes_vigentes(resultado))
        return TemplateLayout.init(self, {
            "colaborador": colaborador, "data": data, "resultado": resultado, "composicao": resultado.composicao, "periodos": custos.PERIODOS,
            "historico": composicao.historico_de_vinculos(colaborador), "form": form, "categorias": custos.CATEGORIAS,
            "pode_personalizar": request.user.has_perm("maodeobra.change_personalizacaocolaborador"),
        })

    @staticmethod
    def _componentes_vigentes(resultado):
        ids = [i.chave for i in resultado.composicao.itens if i.chave]
        return list(ComponenteFuncao.objects.filter(pk__in=ids, funcao=resultado.vinculo.funcao))

    def get(self, request, pk):
        return render(request, "colaborador_composicao.html", self._contexto(request, self._colaborador(request, pk, "view")))

    def post(self, request, pk):
        if not request.user.has_perm("maodeobra.change_personalizacaocolaborador"):
            raise Http404
        colaborador = self._colaborador(request, pk, "change")
        data = data_da_consulta(request)
        resultado = composicao.composicao_do_colaborador(colaborador, data)
        if not resultado.vinculo:
            messages.error(request, "Vincule uma função ao colaborador antes de personalizar itens.")
            return redirect("colaborador-composicao", pk=colaborador.pk)
        form = PersonalizacaoForm(request.POST, vinculo=resultado.vinculo, componentes=self._componentes_vigentes(resultado))
        if form.is_valid():
            with transaction.atomic():
                form.salvar()
                from apps.auditoria.utils import registrar

                registrar(request, acao="editar", modulo="maodeobra", descricao=f"Personalização de custos do colaborador {colaborador.nome}.", silencioso=False)
            messages.success(request, "Personalizações salvas. Campos vazios voltam a herdar o valor da função.")
            return redirect(f"{reverse('colaborador-composicao', args=[colaborador.pk])}?data={data.isoformat()}")
        return render(request, "colaborador_composicao.html", self._contexto(request, colaborador, form))


# --------------------------------------------------------------------------
# Simulações de promoção (planejamento: nunca alteram colaborador nem função)
# --------------------------------------------------------------------------

class SimulacaoListView(_Lista):
    model = SimulacaoPromocao
    template_name = "simulacao.html"
    context_object_name = "simulacoes"
    colunas_ordenaveis = {"nome": "nome", "data": "data_referencia"}
    ordenacao_padrao = "data"
    direcao_padrao = "desc"
    status_padrao = "todos"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("colaborador", "funcao_origem", "funcao_destino")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(colaborador__nome__icontains=termo) | Q(funcao_origem__nome__icontains=termo))
        return self.ordenar(queryset)


class SimulacaoFormMixin(SegmentoMixin):
    model, form_class, template_name = SimulacaoPromocao, SimulacaoForm, "simulacao_form.html"

    def get_success_url(self):
        return reverse("simulacao-detalhe", args=[self.object.pk])


class SimulacaoCreateView(_Form, SimulacaoFormMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"

    def form_valid(self, form):
        form.instance.criado_por = self.request.user
        messages.success(self.request, "Cenário salvo. É um planejamento: nenhum dado real foi alterado.")
        return super().form_valid(form)


class SimulacaoUpdateView(_Form, SimulacaoFormMixin, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Cenário atualizado.")
        return super().form_valid(form)


class SimulacaoDetalheView(SegmentoMixin, ClienteScopedMixin, DetailView):
    model = SimulacaoPromocao
    template_name = "simulacao_detalhe.html"
    context_object_name = "simulacao"

    def get_queryset(self):
        return super().get_queryset().select_related("colaborador", "funcao_origem", "funcao_destino")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["resultado"] = composicao.simular(self.object)
        context["periodos"] = custos.PERIODOS
        return context


class SimulacaoExcluirView(SegmentoMixin, View):
    """Simulação não é referenciada por nada: exclusão física, auditada."""

    def post(self, request, pk):
        with transaction.atomic():
            simulacao = get_object_or_404(SimulacaoPromocao.objects.select_for_update(), pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"))
            registrar_exclusao(request, simulacao)
            simulacao.delete()
        messages.success(request, "Simulação excluída.")
        return redirect("simulacao-list")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def dados_do_dashboard(escopo, filtros, unidade=None):
    """Indicadores dos colaboradores ATIVOS do escopo (filtrados por função/unidade) + resumo dos últimos cenários."""
    colaboradores = Colaborador.objects.filter(cliente__in=escopo, ativo=True).select_related("cliente")
    if unidade is not None and str(unidade).isdigit():
        colaboradores = colaboradores.filter(cliente_id=unidade)
    data = filtros["data"]
    funcao = filtros.get("funcao")
    if funcao:
        colaboradores = colaboradores.filter(vinculos_funcao__funcao=funcao, vinculos_funcao__inicio__lte=data).filter(Q(vinculos_funcao__fim__isnull=True) | Q(vinculos_funcao__fim__gte=data)).distinct()
    indicadores = composicao.indicadores(colaboradores, data, filtros.get("comparar_com"))
    cenarios = []
    for s in SimulacaoPromocao.objects.filter(cliente__in=escopo).select_related("colaborador", "funcao_origem").order_by("-criado_em")[:5]:
        r = composicao.simular(s)
        cenarios.append({"simulacao": s, "quantidade": r["quantidade"], "diferenca_mensal": r["diferenca_mensal"], "percentual": r["percentual"]})
    indicadores["cenarios"] = cenarios
    return indicadores


class DashboardView(SegmentoMixin, View):
    def get(self, request):
        escopo = clientes_dos_itens(request.user, "view")
        form = DashboardForm(request.GET or None, escopo=escopo)
        filtros = form.cleaned_data if form.is_valid() else {"data": timezone.localdate(), "periodo": "mes"}  # filtro inválido = padrões
        indicadores = dados_do_dashboard(escopo, filtros, request.GET.get("unidade"))
        periodo = filtros.get("periodo") or "mes"
        unidades = list(escopo.order_by("tipo_estabelecimento", "nome_razao_social"))
        return render(request, "mao_obra_dashboard.html", TemplateLayout.init(self, {
            "ativa": "dashboard", "filtro": form, "ind": indicadores, "periodo": periodo, "rotulo_periodo": dict(custos.PERIODOS)[periodo], "categorias": custos.CATEGORIAS,
            "unidades": unidades, "mostrar_unidade": len(unidades) > 1, "unidade_selecionada": request.GET.get("unidade", ""),
        }))
