from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_exclusao
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin

from .forms import ItemCompraForm
from .models import ItemCompra
from .services import adicionar_sugestoes, sugestoes_de_compra


class ComprasListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Lista de compras do escopo de unidades do usuário + as sugestões do estoque da PRÓPRIA unidade."""

    model = ItemCompra
    template_name = "compras.html"
    context_object_name = "itens"
    status_padrao = "todos"
    colunas_ordenaveis = {"nome": "descricao", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().select_related("medida", "insumo")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(descricao__icontains=termo) | Q(observacao__icontains=termo))
        origem = self.request.GET.get("origem")
        if origem in dict(ItemCompra.ORIGEM_CHOICES):
            queryset = queryset.filter(origem=origem)
        return self.ordenar(queryset)  # a lista não tem "ativo": itens saem por exclusão

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        cliente = self.request.user.profile.cliente
        context["sugestoes"] = sugestoes_de_compra(cliente) if cliente and self.request.user.has_perm("compras.add_itemcompra") else []
        context["origem"] = self.request.GET.get("origem", "")
        return context


class ItemFormMixin:
    model = ItemCompra
    form_class = ItemCompraForm
    template_name = "compras_form.html"
    success_url = reverse_lazy("compras")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ItemCreateView(ItemFormMixin, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    nome_url_novo = "compras-create"

    def form_valid(self, form):
        form.instance.origem = ItemCompra.ORIGEM_MANUAL
        form.instance.criado_por = self.request.user
        messages.success(self.request, "Item incluído na lista de compras.")
        return super().form_valid(form)


class ItemUpdateView(ItemFormMixin, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Item atualizado.")
        return super().form_valid(form)


class ItemExcluirView(View):
    """Remove o item da lista (exclusão física, auditada na mesma transação)."""

    def post(self, request, pk):
        with transaction.atomic():
            item = get_object_or_404(ItemCompra.objects.select_for_update(), pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"))
            nome = str(item)
            registrar_exclusao(request, item)  # antes do delete(): depois o objeto perde o id
            item.delete()
        messages.success(request, f'Item "{nome}" removido da lista.')
        return redirect("compras")


class AdicionarSugestoesView(View):
    """POST: inclui sugestões na lista da PRÓPRIA unidade. O corpo só diz quais insumos (`insumo`, repetível);
    sem nenhum, inclui todas. A quantidade é sempre recalculada no servidor. Idempotente."""

    def post(self, request):
        cliente = request.user.profile.cliente
        ids = [i for i in request.POST.getlist("insumo") if i.isdigit()]
        if "insumo" in request.POST and not ids:
            messages.error(request, "Seleção inválida.")
            return redirect("compras")
        criados = adicionar_sugestoes(request, cliente, ids or None)
        if criados:
            messages.success(request, f"{len(criados)} item(ns) incluído(s) na lista de compras.")
        else:
            messages.info(request, "Nenhuma sugestão nova para incluir.")
        return redirect("compras")
