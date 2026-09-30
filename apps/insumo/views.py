from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from web_project import TemplateLayout
from web_project.mixins import ClienteScopedMixin

from .exports import exportar_insumos_excel, exportar_insumos_pdf
from .forms import InsumoForm
from .models import Insumo

# Whitelist de colunas ordenáveis: nunca repassamos o valor de "sort" vindo
# da querystring direto para order_by() — só aceitamos um destes nomes
# conhecidos, mapeados para o campo real do model/relacionamento.
COLUNAS_ORDENAVEIS = {
    "nome": "nome",
    "categoria": "categoria__nome",
    "unidade_medida": "unidade_medida",
    "quantidade": "quantidade",
    "preco": "preco",
    "criado_em": "criado_em",
}
PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]


class InsumoFormKwargsMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["cliente"] = self.request.user.profile.cliente
        return kwargs


class InsumoListView(ClienteScopedMixin, ListView):
    """Listagem de Insumos: paginação, pesquisa por nome e ordenação por
    coluna, todas aplicadas no backend (não carrega tudo no navegador)."""

    model = Insumo
    template_name = "insumo.html"
    context_object_name = "insumos"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("categoria").filter(ativo=True)

        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(categoria__nome__icontains=termo))

        campo_pedido = self.request.GET.get("sort", "nome")
        direcao = self.request.GET.get("dir", "asc")
        campo_real = COLUNAS_ORDENAVEIS.get(campo_pedido, "nome")
        if direcao == "desc":
            campo_real = f"-{campo_real}"

        return queryset.order_by(campo_real)

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", PADRAO_POR_PAGINA))
        except (TypeError, ValueError):
            valor = PADRAO_POR_PAGINA
        return valor if valor in OPCOES_POR_PAGINA else PADRAO_POR_PAGINA

    def get(self, request, *args, **kwargs):
        exportacao = request.GET.get("export")
        if exportacao in ("xlsx", "pdf"):
            # Exporta TODOS os registros que batem com o filtro atual (não
            # só a página exibida), mas sempre dentro do mesmo isolamento
            # por Cliente aplicado pelo ClienteScopedMixin.get_queryset().
            queryset = self.get_queryset()
            if exportacao == "xlsx":
                return exportar_insumos_excel(queryset)
            return exportar_insumos_pdf(queryset, request.user, request.GET.get("q", ""))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            {
                "termo_pesquisa": self.request.GET.get("q", ""),
                "per_page": self.get_paginate_by(None),
                "opcoes_por_pagina": OPCOES_POR_PAGINA,
                "sort": self.request.GET.get("sort", "nome"),
                "dir": self.request.GET.get("dir", "asc"),
            }
        )
        return context


class InsumoCreateView(InsumoFormKwargsMixin, ClienteScopedMixin, CreateView):
    model = Insumo
    form_class = InsumoForm
    template_name = "insumo_form.html"
    success_url = reverse_lazy("insumo")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Insumo cadastrado com sucesso.")
        return super().form_valid(form)


class InsumoUpdateView(InsumoFormKwargsMixin, ClienteScopedMixin, UpdateView):
    model = Insumo
    form_class = InsumoForm
    template_name = "insumo_form.html"
    success_url = reverse_lazy("insumo")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Insumo atualizado com sucesso.")
        return super().form_valid(form)


class InsumoExcluirView(View):
    """Exclusão lógica (soft delete): marca ativo=False em vez de apagar
    a linha, para não quebrar históricos/fichas técnicas que referenciem
    este insumo."""

    def post(self, request, pk):
        cliente = request.user.profile.cliente
        # Busca sempre filtrando pelo Cliente do usuário logado — nunca só
        # pelo "pk" da URL — para que um pk de outro cliente resulte em 404
        # em vez de permitir a exclusão (proteção contra IDOR).
        insumo = get_object_or_404(Insumo, pk=pk, cliente=cliente)
        insumo.ativo = False
        insumo.save(update_fields=["ativo", "atualizado_em"])
        messages.success(request, f'Insumo "{insumo.nome}" excluído com sucesso.')
        return redirect("insumo")
