from django.contrib import messages
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_alteracoes, registrar_exclusao
from web_project import TemplateLayout
from web_project.acesso import clientes_acessiveis

from .forms import ModuloForm, SegmentoForm
from .models import ClienteModulo, Modulo, Segmento
from .services import atualizar_modulos_do_cliente, modulos_do_cliente


class ModuloClienteView(TemplateView):
    """Tela "Módulos do Cliente": permite escolher, por Cliente, quais dos
    módulos do SEGMENTO dele ficam habilitados (ex.: um cliente do segmento
    Produto pode ter Ficha Técnica ligada e Precificação desligada)."""

    template_name = "modulo_cliente.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))

        clientes = clientes_acessiveis(self.request.user).select_related("segmento").order_by("nome_razao_social")
        cliente_id = self.request.GET.get("cliente")
        cliente_selecionado = clientes.filter(pk=cliente_id).first() if cliente_id and cliente_id.isdigit() else clientes.first()

        habilitados = set()
        if cliente_selecionado:
            habilitados = set(
                ClienteModulo.objects.filter(cliente=cliente_selecionado, ativo=True).values_list("modulo_id", flat=True)
            )

        context["clientes"] = clientes
        context["cliente_selecionado"] = cliente_selecionado
        context["modulos"] = modulos_do_cliente(cliente_selecionado) if cliente_selecionado else []
        context["modulos_habilitados_ids"] = habilitados
        return context

    def post(self, request, *args, **kwargs):
        # O cliente é resolvido SEMPRE dentro da estrutura do usuário
        # (clientes_acessiveis): um cliente_id de outra estrutura vira 404.
        cliente = get_object_or_404(clientes_acessiveis(request.user), pk=request.POST.get("cliente_id"))
        atualizar_modulos_do_cliente(request, cliente, set(request.POST.getlist("modulos")))
        messages.success(request, f"Módulos atualizados para {cliente.nome_razao_social}.")
        return redirect(f"{request.path}?cliente={cliente.pk}")


# --------------------------------------------------------------------------
# Catálogo de módulos
# --------------------------------------------------------------------------

class ModuloListView(ListView):
    model = Modulo
    template_name = "modulo_list.html"
    context_object_name = "modulos"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ModuloFormMixin:
    model = Modulo
    form_class = ModuloForm
    template_name = "modulo_form.html"
    success_url = reverse_lazy("modulo-catalogo")
    modulo_auditoria = "modulo"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Módulo salvo com sucesso.")
        return super().form_valid(form)


class ModuloCreateView(ModuloFormMixin, AuditoriaFormMixin, CreateView):
    pass


class ModuloUpdateView(ModuloFormMixin, AuditoriaFormMixin, UpdateView):
    pass


class ModuloExcluirView(View):
    def post(self, request, pk):
        modulo = get_object_or_404(Modulo, pk=pk)
        with transaction.atomic():
            registrar_exclusao(request, modulo, modulo="modulo")
            modulo.delete()
        messages.success(request, f'Módulo "{modulo.nome}" excluído.')
        return redirect("modulo-catalogo")


# --------------------------------------------------------------------------
# Segmentos de trabalho
# --------------------------------------------------------------------------

class SegmentoListView(ListView):
    template_name = "segmento_list.html"
    context_object_name = "segmentos"

    def get_queryset(self):
        return Segmento.objects.prefetch_related("modulos")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


def _nomes_dos_modulos(segmento):
    return ", ".join(segmento.modulos.order_by("nome").values_list("nome", flat=True)) or None


class SegmentoFormMixin:
    model = Segmento
    form_class = SegmentoForm
    template_name = "segmento_form.html"
    success_url = reverse_lazy("segmento-list")
    modulo_auditoria = "modulo"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        # AuditoriaFormMixin audita os campos simples; o M2M de módulos é auditado aqui.
        editando = self.object is not None
        antes = _nomes_dos_modulos(self.object) if editando else None
        with transaction.atomic():
            resposta = super().form_valid(form)
            depois = _nomes_dos_modulos(self.object)
            if editando and antes != depois:
                registrar_alteracoes(
                    self.request, self.object,
                    [{"campo": "modulos", "rotulo": "Módulos do segmento", "antes": antes, "depois": depois}],
                    modulo="modulo",
                )
        messages.success(self.request, "Segmento salvo com sucesso.")
        return resposta


class SegmentoCreateView(SegmentoFormMixin, AuditoriaFormMixin, CreateView):
    pass


class SegmentoUpdateView(SegmentoFormMixin, AuditoriaFormMixin, UpdateView):
    pass


class SegmentoExcluirView(View):
    def post(self, request, pk):
        segmento = get_object_or_404(Segmento, pk=pk)
        try:
            with transaction.atomic():
                registrar_exclusao(request, segmento, modulo="modulo")
                segmento.delete()
        except ProtectedError:
            messages.error(request, "Este segmento possui clientes e não pode ser excluído.")
            return redirect("segmento-list")
        messages.success(request, f'Segmento "{segmento.nome}" excluído.')
        return redirect("segmento-list")
