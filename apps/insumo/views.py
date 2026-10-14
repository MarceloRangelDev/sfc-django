from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar as registrar_auditoria
from apps.auditoria.utils import registrar_exclusao
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from apps.processado.services import mensagem_insumo_em_uso
from web_project.mixins import ClienteScopedMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin

from .exports import exportar_insumos_excel, exportar_insumos_pdf
from .forms import InsumoForm
from .models import Insumo

# Whitelist de colunas ordenáveis: nunca repassamos o valor de "sort" vindo
# da querystring direto para order_by() — só aceitamos um destes nomes
# conhecidos, mapeados para o campo real do model/relacionamento.
COLUNAS_ORDENAVEIS = {
    "nome": "nome",
    "medida": "medida__medida",
    "preco": "preco_unidade",  # a coluna "Preço" ordena pelo preço por KG/L/UN
    "preco_unidade": "preco_unidade",
    "criado_em": "criado_em",
}
PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]


class InsumoListView(ListaPorUnidadeMixin, ListView):
    """Listagem de Insumos: paginação, pesquisa por nome e ordenação por
    coluna, todas aplicadas no backend (não carrega tudo no navegador)."""

    model = Insumo
    template_name = "insumo.html"
    context_object_name = "insumos"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("medida", "processado_origem")
        # Filtro de status (junto com pesquisa/paginação, sempre sobre o escopo de unidades):
        # padrão = só ativos; "inativo" lista os excluídos logicamente; "todos" mostra ambos.
        status = self.status_selecionado()
        if status != "todos":
            queryset = queryset.filter(ativo=(status == "ativo"))

        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(marca__icontains=termo) | Q(medida__medida__icontains=termo))

        campo_pedido = self.request.GET.get("sort", "nome")
        direcao = self.request.GET.get("dir", "asc")
        campo_real = COLUNAS_ORDENAVEIS.get(campo_pedido, "nome")
        if direcao == "desc":
            campo_real = f"-{campo_real}"

        return queryset.order_by(campo_real)

    def status_selecionado(self):
        status = self.request.GET.get("status", "ativo")
        return status if status in ("ativo", "inativo", "todos") else "ativo"

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
            registrar_auditoria(
                request, acao="exportar", modulo="insumo",
                descricao=f"Exportação de Insumos em {exportacao} ({queryset.count()} registro(s)).",
            )
            if exportacao == "xlsx":
                return exportar_insumos_excel(queryset)
            filtros = ", ".join(f"{k}={v}" for k, v in request.GET.items() if v and k not in ("export", "page", "per_page", "sort", "dir"))
            return exportar_insumos_pdf(queryset, request.user, filtros)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            {
                "termo_pesquisa": self.request.GET.get("q", ""),
                "status": self.status_selecionado(),
                "per_page": self.get_paginate_by(None),
                "opcoes_por_pagina": OPCOES_POR_PAGINA,
                "sort": self.request.GET.get("sort", "nome"),
                "dir": self.request.GET.get("dir", "asc"),
            }
        )
        return context


class InsumoCreateView(SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model = Insumo
    form_class = InsumoForm
    template_name = "insumo_form.html"
    success_url = reverse_lazy("insumo")
    acao_escopo = "add"
    nome_url_novo = "insumo-create"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        response = super().form_valid(form)  # salva e audita (AuditoriaFormMixin)
        messages.success(self.request, "Insumo cadastrado com sucesso.")
        return response


class InsumoUpdateView(AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model = Insumo
    form_class = InsumoForm
    template_name = "insumo_form.html"
    success_url = reverse_lazy("insumo")
    acao_escopo = "change"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            bloqueio = _redirecionar_se_gerado_por_processado(request, kwargs["pk"], self.clientes_do_escopo())
            if bloqueio:
                return bloqueio
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        response = super().form_valid(form)  # salva e audita (AuditoriaFormMixin)
        messages.success(self.request, "Insumo atualizado com sucesso.")
        return response


def _redirecionar_se_gerado_por_processado(request, pk, escopo):
    """Insumo gerado por um processado só muda pelo processado (senão o valor
    divergiria da ficha técnica e seria sobrescrito no próximo recálculo)."""
    if Insumo.objects.filter(pk=pk, cliente__in=escopo, processado_origem__isnull=False).exists():
        messages.error(request, "Este insumo é gerado por um processado. Altere ou exclua o processado.")
        return redirect("insumo")
    return None


class InsumoExcluirView(View):
    """Exclusão lógica (soft delete): marca ativo=False em vez de apagar
    a linha, para não quebrar históricos/fichas técnicas que referenciem
    este insumo."""

    def post(self, request, pk):
        # Busca sempre dentro do escopo do usuário logado (o próprio cliente e,
        # para a matriz com permissão, as filiais) — nunca só pelo "pk" da URL
        # — para que um pk de fora resulte em 404 (proteção contra IDOR).
        escopo = clientes_dos_itens(request.user, "delete")
        insumo = get_object_or_404(Insumo, pk=pk, cliente__in=escopo)
        bloqueio = _redirecionar_se_gerado_por_processado(request, pk, escopo)
        if bloqueio:
            return bloqueio
        # Insumo em uso por receitas não é excluído: deixaria referência inválida.
        uso = mensagem_insumo_em_uso(insumo)
        if uso:
            messages.error(request, uso)
            return redirect("insumo")
        with transaction.atomic():
            registrar_exclusao(request, insumo, logica=True)
            insumo.ativo = False
            insumo.save(update_fields=["ativo", "atualizado_em"])
        messages.success(request, f'Insumo "{insumo.nome}" excluído com sucesso.')
        return redirect("insumo")


class InsumoPorUuidView(View):
    """Destino do QR Code das etiquetas (`/i/<uuid>`): leva ao insumo no estoque. O UUID sozinho NÃO dá acesso:
    exige login, módulo de estoque habilitado e que o insumo seja do tenant do usuário (escopo de visualização);
    de outra empresa responde 404, igual a um UUID inexistente (não revela que existe)."""

    def get(self, request, uuid):
        insumo = get_object_or_404(Insumo, uuid=uuid, cliente__in=clientes_dos_itens(request.user, "view"))
        return redirect("estoque-insumo", pk=insumo.pk)


class InsumoHistoricoView(TemplateView):
    """Histórico de alterações de UM insumo (preço, quantidade, unidade, mínimo...), a partir da trilha de
    auditoria — só registros desse insumo, dentro do escopo do usuário. Permite filtrar por campo e período e
    mostra a variação percentual dos valores numéricos/monetários."""

    template_name = "insumo_historico.html"

    def get_context_data(self, **kwargs):
        from .historico import montar_historico

        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        insumo = get_object_or_404(
            Insumo.objects.select_related("medida", "cliente"), pk=kwargs["pk"], cliente__in=clientes_dos_itens(self.request.user, "view")
        )
        context.update(insumo=insumo, **montar_historico(insumo, self.request.GET))
        return context
