from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_criacao
from web_project import TemplateLayout
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin

from .forms import ConfiguracaoPdvForm, FormaPagamentoForm, MesaForm
from .models import ConfiguracaoPdv, FormaPagamento, Mesa

FORMAS_PADRAO = [
    ("Dinheiro", FormaPagamento.DINHEIRO), ("PIX", FormaPagamento.PIX), ("Cartão de débito", FormaPagamento.DEBITO), ("Cartão de crédito", FormaPagamento.CREDITO),
]


class _Lista(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    campo_busca = "nome"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(**{f"{self.campo_busca}__icontains": termo}))
        return self.ordenar(self.filtrar_status(queryset))

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class FormaPagamentoListView(_Lista):
    model = FormaPagamento
    template_name = "forma_pagamento.html"
    context_object_name = "formas"


class MesaListView(_Lista):
    model = Mesa
    template_name = "mesa.html"
    context_object_name = "mesas"


class _Form:
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class FormaPagamentoCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = FormaPagamento, FormaPagamentoForm, "forma_pagamento_form.html"
    success_url = reverse_lazy("forma-pagamento-list")
    acao_escopo, nome_url_novo = "add", "forma-pagamento-create"

    def form_valid(self, form):
        messages.success(self.request, "Forma de pagamento cadastrada.")
        return super().form_valid(form)


class FormaPagamentoUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = FormaPagamento, FormaPagamentoForm, "forma_pagamento_form.html"
    success_url = reverse_lazy("forma-pagamento-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Forma de pagamento atualizada. Vendas já feitas mantêm a taxa da época.")
        return super().form_valid(form)


class MesaCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = Mesa, MesaForm, "mesa_form.html"
    success_url = reverse_lazy("mesa-list")
    acao_escopo, nome_url_novo = "add", "mesa-create"

    def form_valid(self, form):
        messages.success(self.request, "Mesa cadastrada.")
        return super().form_valid(form)


class MesaUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = Mesa, MesaForm, "mesa_form.html"
    success_url = reverse_lazy("mesa-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Mesa atualizada.")
        return super().form_valid(form)


class FormasPadraoView(View):
    """POST: cria Dinheiro, PIX, Débito e Crédito (sem taxa) na unidade do usuário, pulando as que já existem pelo nome."""

    def post(self, request):
        cliente = request.user.profile.cliente
        criadas = 0
        with transaction.atomic():
            for nome, tipo in FORMAS_PADRAO:
                if not FormaPagamento.objects.filter(cliente=cliente, nome__iexact=nome).exists():
                    registrar_criacao(request, FormaPagamento.objects.create(cliente=cliente, nome=nome, tipo=tipo))
                    criadas += 1
        messages.success(request, f"{criadas} forma(s) de pagamento criada(s). Ajuste as taxas em cada uma." if criadas else "As formas padrão já existem.")
        return redirect("forma-pagamento-list")


class ConfiguracaoPdvView(_Form, AuditoriaFormMixin, UpdateView):
    """Configurações do PDV da PRÓPRIA unidade do usuário (cada unidade tem as suas)."""

    model, form_class, template_name = ConfiguracaoPdv, ConfiguracaoPdvForm, "pdv_config_form.html"
    success_url = reverse_lazy("pdv-config")

    def get_object(self, queryset=None):
        self.object = ConfiguracaoPdv.da_unidade(self.request.user.profile.cliente)
        from apps.auditoria.utils import tirar_foto

        self._foto_antes = tirar_foto(self.object)
        return self.object

    def get_form_kwargs(self):
        return super().get_form_kwargs()

    def form_valid(self, form):
        messages.success(self.request, "Configurações do PDV salvas.")
        return super().form_valid(form)
