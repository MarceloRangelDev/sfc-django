"""Telas do cadastro global de insumos e da fila de insumos fora do padrão (somente quem tem as permissões; por padrão, o Administrador)."""
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_alteracoes
from apps.insumo.models import Insumo
from web_project import TemplateLayout
from web_project.regras import RegraNegocioError

from . import services
from .forms import InsumoGlobalForm
from .models import InsumoGlobal, InsumoGlobalMarca


class InsumoGlobalListView(ListView):
    template_name = "insumo_global.html"
    context_object_name = "globais"
    paginate_by = 25

    def get_queryset(self):
        queryset = InsumoGlobal.objects.select_related("medida").prefetch_related("segmentos", "variacoes__marca").annotate(usos=Count("insumos", distinct=True), qtd_marcas=Count("variacoes", distinct=True))
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo))
        return queryset.order_by("nome")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["termo_pesquisa"] = self.request.GET.get("q", "")
        return context


class _Formulario:
    model, form_class, template_name = InsumoGlobal, InsumoGlobalForm, "insumo_global_form.html"
    success_url = reverse_lazy("insumo-global")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        marca = form.cleaned_data.get("marca", "")
        if form.existente is not None:  # mesmo nome padrão, outra marca: acrescenta a variação ao padrão existente
            try:
                variacao, criada = services.adicionar_marca(self.request, form.existente, marca)
            except RegraNegocioError as erro:
                form.add_error("marca", str(erro))
                return self.form_invalid(form)
            escolhidos = form.cleaned_data.get("segmentos")
            if escolhidos and form.existente.segmentos.exists():
                form.existente.segmentos.add(*escolhidos)  # soma aos segmentos atuais (sem segmento = todos: não restringe)
            messages.success(self.request, f"Marca “{variacao.marca.nome}” adicionada a “{form.existente.nome}”." if criada else f"“{variacao.marca.nome}” já estava em “{form.existente.nome}”.")
            return redirect("insumo-global-edit", pk=form.existente.pk)
        if form.instance.pk is None:
            form.instance.criado_por = self.request.user
        resposta = super().form_valid(form)  # salva e audita
        if marca:
            try:
                services.adicionar_marca(self.request, self.object, marca)
            except RegraNegocioError as erro:
                messages.error(self.request, str(erro))
        messages.success(self.request, "Insumo global salvo.")
        return resposta


class InsumoGlobalCreateView(_Formulario, AuditoriaFormMixin, CreateView):
    def get_success_url(self):
        # Depois de criar, abre a edição: é lá que se envia a foto (precisa do registro salvo).
        messages.info(self.request, "Agora você pode adicionar a foto do insumo.")
        return reverse("insumo-global-edit", args=[self.object.pk])


class InsumoGlobalUpdateView(_Formulario, AuditoriaFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["variacoes"] = self.object.variacoes.select_related("marca")
        return context


class VariacaoAdicionarView(View):
    """POST `marca` (nome): cria a marca global se não existir e a variação deste insumo padrão com ela."""

    def post(self, request, pk):
        item = get_object_or_404(InsumoGlobal, pk=pk)
        try:
            variacao, criada = services.adicionar_marca(request, item, request.POST.get("marca", ""))
            messages.success(request, f"Marca “{variacao.marca.nome}” adicionada. Envie a foto desta marca." if criada else f"“{variacao.marca.nome}” já estava na lista.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("insumo-global-edit", pk=item.pk)


class VariacaoAlternarView(View):
    """Ativa/inativa a variação (insumos de clientes já vinculados a ela continuam; ela só deixa de ser oferecida)."""

    def post(self, request, pk):
        variacao = get_object_or_404(InsumoGlobalMarca.objects.select_related("marca"), pk=pk)
        antes = "ativa" if variacao.ativo else "inativa"
        variacao.ativo = not variacao.ativo
        variacao.save(update_fields=["ativo"])
        registrar_alteracoes(request, variacao, [{"campo": "ativo", "rotulo": "Situação", "antes": antes, "depois": "ativa" if variacao.ativo else "inativa"}], modulo="insumoglobal")
        return redirect("insumo-global-edit", pk=variacao.insumo_global_id)


class InsumoGlobalAlternarView(View):
    """Ativa/inativa um padrão (não se apaga: insumos de clientes podem estar vinculados a ele)."""

    def post(self, request, pk):
        item = get_object_or_404(InsumoGlobal, pk=pk)
        antes = "ativo" if item.ativo else "inativo"
        item.ativo = not item.ativo
        item.save(update_fields=["ativo", "atualizado_em"])
        registrar_alteracoes(request, item, [{"campo": "ativo", "rotulo": "Situação", "antes": antes, "depois": "ativo" if item.ativo else "inativo"}], modulo="insumoglobal")
        messages.success(request, "Padrão ativado." if item.ativo else "Padrão inativado.")
        return redirect("insumo-global")


class NaoConformesView(ListView):
    """Insumos de TODAS as unidades que não existem no cadastro global, com sugestões de padrão para redirecionar."""

    template_name = "insumo_nao_conformes.html"
    context_object_name = "insumos"
    paginate_by = 25

    def get_queryset(self):
        queryset = Insumo.objects.filter(nao_conforme=True, ativo=True).select_related("cliente", "medida", "insumo_global").order_by("criado_em")
        termo = self.request.GET.get("q", "").strip()
        return queryset.filter(Q(nome__icontains=termo) | Q(cliente__nome_razao_social__icontains=termo)) if termo else queryset

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        for insumo in context["insumos"]:
            if insumo.insumo_global_id:  # marca personalizada: as marcas do próprio padrão são o destino possível
                insumo.marcas_destino = list(insumo.insumo_global.variacoes.filter(ativo=True).select_related("marca"))
            else:
                insumo.sugestoes = services.semelhantes(insumo.nome, limite=3, corte=0.6)
        context["globais"] = InsumoGlobal.objects.filter(ativo=True).order_by("nome")
        context["termo_pesquisa"] = self.request.GET.get("q", "")
        return context


class PromoverView(View):
    def post(self, request, pk):
        insumo = get_object_or_404(Insumo, pk=pk, nao_conforme=True)
        try:
            novo = services.promover_a_global(request, insumo)
            messages.success(request, f"“{novo.nome}” agora é um insumo padrão.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("insumo-nao-conformes")


class RedirecionarView(View):
    """POST `destino` = id do padrão. Renomeia o insumo para o padrão ou, se a unidade já o tem, mescla e exclui o duplicado."""

    def post(self, request, pk):
        insumo = get_object_or_404(Insumo, pk=pk, nao_conforme=True)
        destino = InsumoGlobal.objects.filter(pk=request.POST.get("destino") or 0).first()
        if destino is None:
            messages.error(request, "Escolha o insumo padrão de destino.")
            return redirect("insumo-nao-conformes")
        try:
            nome = insumo.nome
            services.redirecionar(request, insumo, destino)
            messages.success(request, f"“{nome}” foi redirecionado para “{destino.nome}”.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("insumo-nao-conformes")


class PromoverMarcaView(View):
    def post(self, request, pk):
        insumo = get_object_or_404(Insumo, pk=pk, nao_conforme=True, insumo_global__isnull=False)
        try:
            variacao = services.promover_marca(request, insumo)
            messages.success(request, f"“{variacao.marca.nome}” agora é uma marca padrão de “{variacao.insumo_global.nome}”.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("insumo-nao-conformes")


class RedirecionarMarcaView(View):
    """POST `destino` = id da variação (marca) do mesmo insumo padrão."""

    def post(self, request, pk):
        insumo = get_object_or_404(Insumo, pk=pk, nao_conforme=True, insumo_global__isnull=False)
        variacao = InsumoGlobalMarca.objects.filter(pk=request.POST.get("destino") or 0).select_related("marca", "insumo_global").first()
        if variacao is None:
            messages.error(request, "Escolha a marca de destino.")
            return redirect("insumo-nao-conformes")
        try:
            services.redirecionar_marca(request, insumo, variacao)
            messages.success(request, f"A marca foi redirecionada para “{variacao.marca.nome}”.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("insumo-nao-conformes")
