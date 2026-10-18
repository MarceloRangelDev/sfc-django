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
from .models import InsumoGlobal


class InsumoGlobalListView(ListView):
    template_name = "insumo_global.html"
    context_object_name = "globais"
    paginate_by = 25

    def get_queryset(self):
        queryset = InsumoGlobal.objects.select_related("medida").annotate(usos=Count("insumos"))
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
        if form.instance.pk is None:
            form.instance.criado_por = self.request.user
        resposta = super().form_valid(form)  # salva e audita
        messages.success(self.request, "Insumo global salvo.")
        return resposta


class InsumoGlobalCreateView(_Formulario, AuditoriaFormMixin, CreateView):
    def get_success_url(self):
        # Depois de criar, abre a edição: é lá que se envia a foto (precisa do registro salvo).
        messages.info(self.request, "Agora você pode adicionar a foto do insumo.")
        return reverse("insumo-global-edit", args=[self.object.pk])


class InsumoGlobalUpdateView(_Formulario, AuditoriaFormMixin, UpdateView):
    pass


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
        queryset = Insumo.objects.filter(nao_conforme=True, ativo=True).select_related("cliente", "medida").order_by("criado_em")
        termo = self.request.GET.get("q", "").strip()
        return queryset.filter(Q(nome__icontains=termo) | Q(cliente__nome_razao_social__icontains=termo)) if termo else queryset

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        for insumo in context["insumos"]:
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
