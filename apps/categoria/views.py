from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_edicao, tirar_foto
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ExcluirOuDesativarView, ListaPorUnidadeMixin, SalvarEAdicionarMixin
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .calculos import percentual_do_total, ticket_medio_centavos
from .forms import CategoriaForm
from .models import Categoria


class CategoriaListView(ListaPorUnidadeMixin, ListView):
    """Categorias com quantidade vendida, faturamento, ticket médio e % do
    faturamento total. O total considera o MESMO filtro/escopo da lista
    (todas as páginas), então os percentuais somam 100 % do que está filtrado."""

    model = Categoria
    template_name = "categoria.html"
    context_object_name = "categorias"
    paginate_by = 25

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo))
        status = self.request.GET.get("status")
        if status == "ativo":
            queryset = queryset.filter(ativo=True)
        elif status == "inativo":
            queryset = queryset.filter(ativo=False)
        return queryset.order_by("nome", "pk")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        totais = self.get_queryset().aggregate(faturamento=Sum("faturamento"), quantidade=Sum("quantidade_vendida"))
        total_fat, total_qtd = totais["faturamento"] or 0, totais["quantidade"] or 0
        for categoria in context["categorias"]:  # só a página atual
            categoria.ticket_medio = DinheiroService.formatar(ticket_medio_centavos(categoria.faturamento, categoria.quantidade_vendida))
            categoria.percentual = percentual_do_total(categoria.faturamento, total_fat)
            categoria.faturamento_formatado = DinheiroService.formatar(categoria.faturamento)
        context.update(
            termo_pesquisa=self.request.GET.get("q", ""), status=self.request.GET.get("status", ""),
            total_quantidade=total_qtd, total_faturamento=DinheiroService.formatar(total_fat),
            total_ticket=DinheiroService.formatar(ticket_medio_centavos(total_fat, total_qtd)),
        )
        return context


class CategoriaCreateView(SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = "categoria_form.html"
    success_url = reverse_lazy("categoria")
    acao_escopo = "add"
    nome_url_novo = "categoria-create"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Categoria cadastrada com sucesso.")
        return super().form_valid(form)


class CategoriaUpdateView(AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = "categoria_form.html"
    success_url = reverse_lazy("categoria")
    acao_escopo = "change"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Categoria atualizada com sucesso.")
        return super().form_valid(form)


class CategoriaAlternarAtivoView(View):
    """Ativa/desativa. Categoria desativada some dos formulários novos, mas
    produtos, fichas e precificações que já a usam continuam intactos (sem cascata)."""

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, cliente__in=clientes_dos_itens(request.user, "change"))
        with transaction.atomic():
            foto = tirar_foto(categoria)
            categoria.ativo = not categoria.ativo
            categoria.save(update_fields=["ativo", "atualizado_em"])
            registrar_edicao(request, categoria, foto)
        messages.success(request, f'Categoria "{categoria.nome}" {"ativada" if categoria.ativo else "desativada"}.')
        return redirect("categoria")


class CategoriaExcluirView(ExcluirOuDesativarView):
    """Sem uso -> apagada; em uso (insumos antigos, produtos...) -> só desativada."""

    model = Categoria
    url_lista = "categoria"
    rotulo = "categoria"
    artigo = "a"


class CategoriaCalcularView(View):
    """Prévia (sem gravar) do ticket médio e do % do faturamento enquanto o
    formulário é preenchido. Usa as mesmas funções da gravação/lista.
    GET /categoria/calcular?quantidade=10&faturamento=1.500,00&pk=<id opcional>"""

    def get(self, request):
        resposta = {"ticket_medio": DinheiroService.formatar(0), "percentual": "0,00"}
        try:
            quantidade = int(request.GET.get("quantidade") or 0)
            faturamento = DinheiroService.de_texto(request.GET.get("faturamento") or "0")
            if quantidade < 0 or faturamento < 0:
                raise ValueError
            resposta["ticket_medio"] = DinheiroService.formatar(ticket_medio_centavos(faturamento, quantidade))
            # Total = categorias do escopo do usuário (sem esta, se estiver sendo editada) + este valor.
            outras = Categoria.objects.filter(cliente__in=clientes_dos_itens(request.user, "view"))
            if request.GET.get("pk", "").isdigit():
                outras = outras.exclude(pk=request.GET["pk"])
            total = (outras.aggregate(t=Sum("faturamento"))["t"] or 0) + faturamento
            resposta["percentual"] = str(percentual_do_total(faturamento, total)).replace(".", ",")
        except (ValueError, TypeError, RegraNegocioError):
            pass  # entrada incompleta: devolve valores neutros
        return JsonResponse(resposta)
