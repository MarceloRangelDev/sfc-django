from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar as registrar_auditoria
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import ClienteScopedMixin, ExcluirOuDesativarView, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin
from web_project.regras.dinheiro import DinheiroService

from .calculo import texto_horas
from .forms import ApontamentoForm, ColaboradorForm, ConfiguracaoForm, PeriodoForm
from .models import Apontamento, Colaborador, ConfiguracaoMaoDeObra, segmento_permite


class SegmentoMixin:
    """Mão de obra só existe para empresas de segmento Serviço ou Produto e Serviço (decisão do cliente): as demais recebem 403."""

    def dispatch(self, request, *args, **kwargs):
        cliente = request.user.profile.cliente if request.user.is_authenticated else None
        if cliente is not None and not segmento_permite(cliente):
            raise PermissionDenied("O segmento da sua empresa não usa mão de obra por hora/dia.")
        return super().dispatch(request, *args, **kwargs)


class _Lista(SegmentoMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ColaboradorListView(_Lista):
    model = Colaborador
    template_name = "colaborador.html"
    context_object_name = "colaboradores"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(funcao__icontains=termo))
        return self.ordenar(self.filtrar_status(queryset))


class _Form(SegmentoMixin):
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ColaboradorCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = Colaborador, ColaboradorForm, "colaborador_form.html"
    success_url = reverse_lazy("colaborador-list")
    acao_escopo, nome_url_novo = "add", "colaborador-create"

    def form_valid(self, form):
        messages.success(self.request, "Colaborador cadastrado.")
        return super().form_valid(form)


class ColaboradorUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = Colaborador, ColaboradorForm, "colaborador_form.html"
    success_url = reverse_lazy("colaborador-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Colaborador atualizado. Apontamentos antigos mantêm o valor da época.")
        return super().form_valid(form)


class ColaboradorExcluirView(SegmentoMixin, ExcluirOuDesativarView):
    """Sem apontamentos -> apagado; com apontamentos -> só desativado (o histórico não se perde)."""

    model, url_lista, rotulo, artigo = Colaborador, "colaborador-list", "colaborador", "o"


class ApontamentoListView(_Lista):
    model = Apontamento
    template_name = "apontamento.html"
    context_object_name = "apontamentos"
    colunas_ordenaveis = {"data": "data", "colaborador": "colaborador__nome", "total": "valor_total"}
    ordenacao_padrao = "data"
    direcao_padrao = "desc"
    status_padrao = "todos"

    def filtros(self):
        form = PeriodoForm(self.request.GET or None, escopo=clientes_dos_itens(self.request.user, "view"))
        return form

    def get_queryset(self):
        queryset = super().get_queryset().select_related("colaborador", "servico")
        form = self.filtros()
        if form.is_valid():
            d = form.cleaned_data
            if d.get("inicio"):
                queryset = queryset.filter(data__gte=d["inicio"])
            if d.get("fim"):
                queryset = queryset.filter(data__lte=d["fim"])
            if d.get("colaborador"):
                queryset = queryset.filter(colaborador=d["colaborador"])
        return self.ordenar(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filtro"] = self.filtros()
        context["total_pagina"] = sum(a.valor_total for a in context["apontamentos"])
        return context


class ApontamentoFormMixin:
    model, form_class, template_name = Apontamento, ApontamentoForm, "apontamento_form.html"
    success_url = reverse_lazy("apontamento-list")

    def form_valid(self, form):
        if form.instance.pk is None:
            form.instance.criado_por = self.request.user
        return super().form_valid(form)


class ApontamentoCreateView(_Form, ApontamentoFormMixin, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo, nome_url_novo = "add", "apontamento-create"

    def form_valid(self, form):
        messages.success(self.request, f"Apontamento registrado: {form.instance.valor_total_formatado}.")
        return super().form_valid(form)

    def get_initial(self):
        return {"data": timezone.localdate(), "tipo": Apontamento.HORA}


class ApontamentoUpdateView(_Form, ApontamentoFormMixin, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Apontamento recalculado com as regras e o valor da hora de hoje.")
        return super().form_valid(form)


class ApontamentoExcluirView(SegmentoMixin, View):
    """Exclusão física, auditada (o apontamento não é referenciado por outros registros)."""

    def post(self, request, pk):
        from django.db import transaction

        from apps.auditoria.utils import registrar_exclusao

        with transaction.atomic():
            apontamento = get_object_or_404(Apontamento.objects.select_for_update(), pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"))
            registrar_exclusao(request, apontamento)
            apontamento.delete()
        messages.success(request, "Apontamento excluído.")
        return redirect("apontamento-list")


class ConfiguracaoView(_Form, AuditoriaFormMixin, UpdateView):
    """Regras de cálculo da PRÓPRIA unidade (arredondamento, jornada, hora extra, domingo)."""

    model, form_class, template_name = ConfiguracaoMaoDeObra, ConfiguracaoForm, "mao_obra_config.html"
    success_url = reverse_lazy("mao-obra-config")

    def get_object(self, queryset=None):
        from apps.auditoria.utils import tirar_foto

        self.object = ConfiguracaoMaoDeObra.da_unidade(self.request.user.profile.cliente)
        self._foto_antes = tirar_foto(self.object)
        return self.object

    def form_valid(self, form):
        messages.success(self.request, "Regras salvas. Valem para os próximos apontamentos; os antigos não mudam.")
        return super().form_valid(form)


def linhas_do_relatorio(consulta):
    """Resumo por colaborador: horas, horas extras, dias e valor (somente leitura; vem do snapshot dos apontamentos)."""
    resumo = (
        consulta.values("colaborador_id", "colaborador__nome").annotate(
            qtd=Count("pk"), minutos=Sum("minutos_trabalhados"), extra=Sum("minutos_extra"), normal=Sum("valor_normal"), valor_extra=Sum("valor_extra"), total=Sum("valor_total"),
        ).order_by("colaborador__nome")
    )
    dias = {r["colaborador_id"]: r["d"] for r in consulta.filter(tipo=Apontamento.DIA).values("colaborador_id").annotate(d=Sum("dias"))}
    linhas = []
    for r in resumo:
        r = {**r, "dias": dias.get(r["colaborador_id"]) or 0}
        r.update(horas_txt=texto_horas(r["minutos"] or 0), extra_txt=texto_horas(r["extra"] or 0), normal_fmt=DinheiroService.formatar(r["normal"] or 0),
                 extra_fmt=DinheiroService.formatar(r["valor_extra"] or 0), total_fmt=DinheiroService.formatar(r["total"] or 0))
        linhas.append(r)
    return linhas


class RelatorioView(SegmentoMixin, View):
    """Relatório por período (folha de apontamentos): tela, Excel e PDF, no escopo do usuário (tenant)."""

    def get(self, request):
        escopo = clientes_dos_itens(request.user, "view")
        form = PeriodoForm(request.GET or None, escopo=escopo)
        consulta = Apontamento.objects.filter(cliente__in=escopo)
        filtros = ""
        if form.is_valid():
            d = form.cleaned_data
            if d.get("inicio"):
                consulta = consulta.filter(data__gte=d["inicio"])
            if d.get("fim"):
                consulta = consulta.filter(data__lte=d["fim"])
            if d.get("colaborador"):
                consulta = consulta.filter(colaborador=d["colaborador"])
            filtros = ", ".join(f"{k}={v}" for k, v in request.GET.items() if v and k != "export")
        linhas = linhas_do_relatorio(consulta)
        total = sum(l["total"] or 0 for l in linhas)
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            registrar_auditoria(request, acao="exportar", modulo="maodeobra", descricao=f"Relatório de mão de obra exportado em {formato}.", silencioso=False)
            cabecalho = ["Colaborador", "Apontamentos", "Horas trabalhadas", "Horas extras", "Dias", "Valor normal", "Valor das extras", "Total"]
            tabela = [
                [l["colaborador__nome"], l["qtd"], texto_horas(l["minutos"] or 0), texto_horas(l["extra"] or 0), f"{l['dias']:.1f}".replace(".", ","),
                 DinheiroService.formatar((l["normal"] or 0)), DinheiroService.formatar(l["valor_extra"] or 0), DinheiroService.formatar(l["total"] or 0)]
                for l in linhas
            ] + [["TOTAL", "", "", "", "", "", "", DinheiroService.formatar(total)]]
            if formato == "xlsx":
                return exportar_excel("mao-de-obra", "Mão de obra", cabecalho, [[sanitizar_texto_excel(c) if isinstance(c, str) else c for c in l] for l in tabela])
            return exportar_pdf("mao-de-obra", "Relatório de mão de obra", request.user, cabecalho, tabela, filtros)
        from django.shortcuts import render

        return render(request, "mao_obra_relatorio.html", TemplateLayout.init(self, {
            "ativa": "relatorio", "filtro": form, "linhas": linhas, "total": total, "total_fmt": DinheiroService.formatar(total),
        }))
