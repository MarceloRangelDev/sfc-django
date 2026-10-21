from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_criacao
from apps.cliente.models import Cliente
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
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


class _SolicitanteNoFormulario:
    """Entrega o usuário ao formulário: ele decide se os campos de forma global aparecem (só para o administrador do sistema)."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["solicitante"] = self.request.user
        return kwargs


class FormaPagamentoListView(_Lista):
    model = FormaPagamento
    template_name = "forma_pagamento.html"
    context_object_name = "formas"
    incluir_globais = True  # formas globais liberadas ao segmento do usuário aparecem (somente leitura)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filiais_padrao"] = filiais_para_formas(self.request.user) if self.request.user.profile.cliente else []
        return contexto


class MesaListView(_Lista):
    model = Mesa
    template_name = "mesa.html"
    context_object_name = "mesas"


class _Form:
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class FormaPagamentoCreateView(_SolicitanteNoFormulario, _Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = FormaPagamento, FormaPagamentoForm, "forma_pagamento_form.html"
    success_url = reverse_lazy("forma-pagamento-list")
    acao_escopo, nome_url_novo = "add", "forma-pagamento-create"

    def form_valid(self, form):
        messages.success(self.request, "Forma de pagamento cadastrada.")
        return super().form_valid(form)


class FormaPagamentoUpdateView(_SolicitanteNoFormulario, _Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
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


def filiais_para_formas(user):
    """Filiais ativas que o usuário pode abastecer com as formas padrão (só matriz com `cliente.add_itensfilial`)."""
    cliente = user.profile.cliente
    return clientes_dos_itens(user, "add").exclude(pk=cliente.pk) if cliente else Cliente.objects.none()


def _criar_formas_padrao(request, cliente):
    criadas = 0
    for nome, tipo in FORMAS_PADRAO:
        if not FormaPagamento.objects.filter(cliente=cliente, nome__iexact=nome).exists():
            registrar_criacao(request, FormaPagamento.objects.create(cliente=cliente, nome=nome, tipo=tipo))
            criadas += 1
    return criadas


class FormasPadraoView(View):
    """POST: cria Dinheiro, PIX, Débito e Crédito (sem taxa) na unidade do usuário, pulando as que já existem pelo nome.
    `filiais` = "todas" | "escolher" (com `filial` = ids) estende às filiais que o usuário alcança; qualquer outro valor = só a unidade."""

    def post(self, request):
        cliente = request.user.profile.cliente
        alvos = [cliente]
        modo = request.POST.get("filiais", "")
        if modo in ("todas", "escolher"):
            disponiveis = filiais_para_formas(request.user)
            if modo == "escolher":
                ids = [int(i) for i in request.POST.getlist("filial") if i.isdigit()]
                disponiveis = disponiveis.filter(pk__in=ids)
            alvos += list(disponiveis)
        criadas = 0
        with transaction.atomic():
            for alvo in alvos:
                criadas += _criar_formas_padrao(request, alvo)
        unidades = f" em {len(alvos)} unidade(s)" if len(alvos) > 1 else ""
        messages.success(request, f"{criadas} forma(s) de pagamento criada(s){unidades}. Ajuste as taxas em cada uma." if criadas else "As formas padrão já existem.")
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
