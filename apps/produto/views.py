from django.contrib import messages
from django.db.models import Q
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar as registrar_auditoria
from apps.categoria.models import Categoria
from web_project import TemplateLayout
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import (
    ClienteScopedMixin,
    ExcluirOuDesativarView,
    ListaPaginadaMixin,
    ListaPorUnidadeMixin,
    SalvarEAdicionarMixin,
)

from .forms import ProdutoForm
from .models import Produto

CABECALHO_EXPORTACAO = ["Produto", "Categoria", "Descrição", "Unidade", "Status", "Cadastrado em"]


class ProdutoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Produtos: filtros (pesquisa, categoria, status, unidade), ordenação,
    paginação e exportação Excel/PDF — tudo sobre o escopo de unidades do
    usuário; a exportação usa exatamente o queryset filtrado da tela."""

    model = Produto
    template_name = "produto.html"
    context_object_name = "produtos"
    colunas_ordenaveis = {"nome": "nome", "categoria": "categoria__nome", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().select_related("categoria")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo) | Q(categoria__nome__icontains=termo))
        categoria = self.request.GET.get("categoria", "")
        if categoria.isdigit():
            queryset = queryset.filter(categoria_id=categoria)
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(
                request, acao="exportar", modulo="produto",
                descricao=f"Exportação de Produtos em {formato} ({queryset.count()} registro(s)).",
            )
            return self.exportar(formato, queryset)
        return super().get(request, *args, **kwargs)

    def exportar(self, formato, queryset):
        linhas = [
            [
                p.nome, p.categoria.nome, p.descricao, p.cliente.nome_razao_social,
                "Ativo" if p.ativo else "Inativo", timezone.localtime(p.criado_em).strftime("%d/%m/%Y %H:%M"),
            ]
            for p in queryset.select_related("cliente")
        ]
        if formato == "xlsx":
            return exportar_excel("produtos", "Produtos", CABECALHO_EXPORTACAO, [[sanitizar_texto_excel(c) for c in l] for l in linhas])
        filtros = ", ".join(f"{k}={v}" for k, v in self.request.GET.items() if v and k not in ("export", "page", "per_page"))
        return exportar_pdf("produtos", "Relatório de Produtos", self.request.user, CABECALHO_EXPORTACAO, linhas, filtros)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["categorias"] = Categoria.objects.filter(cliente__in=self.clientes_do_escopo()).order_by("nome")
        context["categoria_selecionada"] = self.request.GET.get("categoria", "")
        return context


class ProdutoCreateView(SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "produto_form.html"
    success_url = reverse_lazy("produto")
    acao_escopo = "add"
    nome_url_novo = "produto-create"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Produto cadastrado com sucesso.")
        return super().form_valid(form)


class ProdutoUpdateView(AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "produto_form.html"
    success_url = reverse_lazy("produto")
    acao_escopo = "change"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Produto atualizado com sucesso.")
        return super().form_valid(form)


class ProdutoExcluirView(ExcluirOuDesativarView):
    """Sem fichas/precificações -> apagado; em uso -> só desativado."""

    model = Produto
    url_lista = "produto"
    rotulo = "produto"
    artigo = "o"
