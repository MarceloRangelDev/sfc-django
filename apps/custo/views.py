from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar as registrar_auditoria
from apps.auditoria.utils import tirar_foto
from apps.categoria.models import Categoria
from web_project import TemplateLayout
from apps.auditoria.utils import registrar_exclusao
from web_project.acesso import administrador_global, clientes_dos_itens
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import (
    ClienteScopedMixin, ExcluirOuDesativarView, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin, em_uso,
)
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .forms import CustoComumForm, CustoForm
from .models import TIPO_CHOICES, TIPO_FIXO, TIPO_VARIAVEL, CustoModelo, CustoOperacional
from .services import ajustar_categoria, distribuicao_por_categoria, restaurar_categoria, salvar_custo

CABECALHO_EXPORTACAO = ["Custo", "Tipo", "Valor mensal", "Origem", "Unidade", "Status"]


class CustoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Custos operacionais: filtros (pesquisa, tipo, status, unidade), ordenação,
    paginação, totais de fixos/variáveis e exportação Excel/PDF sobre o escopo
    de unidades do usuário."""

    model = CustoOperacional
    template_name = "custo.html"
    context_object_name = "custos"
    colunas_ordenaveis = {"nome": "nome", "tipo": "tipo", "valor": "valor", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().select_related("modelo")
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo))
        if params.get("tipo") in (TIPO_FIXO, TIPO_VARIAVEL):
            queryset = queryset.filter(tipo=params["tipo"])
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(
                request, acao="exportar", modulo="custo",
                descricao=f"Exportação de Custos operacionais em {formato} ({queryset.count()} registro(s)).",
            )
            return self.exportar(formato, queryset)
        return super().get(request, *args, **kwargs)

    def exportar(self, formato, queryset):
        linhas = [
            [
                c.nome, c.get_tipo_display(), c.valor_formatado, c.modelo.nome if c.modelo_id else "Próprio",
                c.cliente.nome_razao_social, "Ativo" if c.ativo else "Inativo",
            ]
            for c in queryset.select_related("cliente")
        ]
        if formato == "xlsx":
            return exportar_excel("custos-operacionais", "Custos operacionais", CABECALHO_EXPORTACAO,
                                  [[sanitizar_texto_excel(c) for c in l] for l in linhas])
        filtros = ", ".join(f"{k}={v}" for k, v in self.request.GET.items() if v and k not in ("export", "page", "per_page"))
        return exportar_pdf("custos-operacionais", "Relatório de Custos operacionais", self.request.user, CABECALHO_EXPORTACAO, linhas, filtros)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        # Totais do que está filtrado (todas as páginas), separados por tipo.
        por_tipo = dict(self.get_queryset().order_by().values_list("tipo").annotate(t=Sum("valor")))
        fixo, variavel = por_tipo.get(TIPO_FIXO) or 0, por_tipo.get(TIPO_VARIAVEL) or 0
        cliente = self.request.user.profile.cliente
        usados = CustoOperacional.objects.filter(cliente=cliente, modelo__isnull=False).values_list("modelo_id", flat=True)
        context.update(
            tipos=TIPO_CHOICES, tipo_selecionado=self.request.GET.get("tipo", ""),
            total_fixo=DinheiroService.formatar(fixo), total_variavel=DinheiroService.formatar(variavel),
            total_geral=DinheiroService.formatar(fixo + variavel),
            # Custos comuns (padrão do sistema + próprios) que a unidade do usuário ainda não adicionou.
            modelos_disponiveis=CustoModelo.objects.filter(Q(cliente__isnull=True) | Q(cliente=cliente), ativo=True).exclude(pk__in=list(usados)),
        )
        return context


class CustoFormMixin:
    """Formulário do custo operacional (a distribuição por categoria é automática)."""

    model = CustoOperacional
    form_class = CustoForm
    template_name = "custo_form.html"
    success_url = reverse_lazy("custo-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        custo = form.instance
        if custo.pk is None:  # só ao criar: o dono é a unidade do usuário
            custo.cliente = self.request.user.profile.cliente
        salvar_custo(self.request, custo, foto_antes=getattr(self, "_foto_antes", None))
        self.object = custo
        messages.success(self.request, self.mensagem_sucesso)
        if "salvar_e_novo" in self.request.POST and self.object_era_novo(form):
            return redirect("custo-create")
        return redirect(self.success_url)

    def object_era_novo(self, form):
        return isinstance(self, CreateView)


class CustoCreateView(CustoFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    mensagem_sucesso = "Custo operacional cadastrado com sucesso."

    def get_initial(self):
        """?modelo=<id>: abre o formulário já com o custo comum escolhido (padrão do sistema ou da unidade)."""
        inicial = super().get_initial()
        escolhido = self.request.GET.get("modelo", "")
        cliente = self.request.user.profile.cliente
        modelo = (
            CustoModelo.objects.filter(Q(cliente__isnull=True) | Q(cliente=cliente), pk=escolhido, ativo=True).first()
            if escolhido.isdigit() else None
        )
        if modelo:
            inicial.update(modelo=modelo.pk, nome=modelo.nome, tipo=modelo.tipo)
        return inicial


class CustoUpdateView(CustoFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"
    mensagem_sucesso = "Custo operacional atualizado com sucesso."

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)  # antes de o formulário alterar o objeto
        return objeto


class CustoExcluirView(ExcluirOuDesativarView):
    """Custo não tem dependentes (a distribuição é parte dele): é apagado."""

    model = CustoOperacional
    url_lista = "custo-list"
    rotulo = "custo"
    artigo = "o"


class CustoCategoriasView(TemplateView):
    """Como o custo operacional chega a cada categoria, em R$: calculado pelas vendas e, quando o
    usuário decide, ajustado manualmente. Mostra UMA unidade por vez (a do usuário; a matriz
    escolhe uma filial se tiver permissão), para nunca misturar custos de unidades diferentes."""

    template_name = "custo_categorias.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        unidades = list(clientes_dos_itens(self.request.user, "view").order_by("tipo_estabelecimento", "nome_razao_social"))
        escolhida = self.request.GET.get("unidade", "")
        unidade = next((u for u in unidades if str(u.pk) == escolhida), None) or self.request.user.profile.cliente
        linhas, totais = distribuicao_por_categoria(unidade)
        formatar = DinheiroService.formatar
        pode_ajustar = self.request.user.has_perm("custo.change_custooperacional") and unidade in set(clientes_dos_itens(self.request.user, "change"))
        for linha in linhas:
            linha["valor_fmt"] = formatar(linha["valor"])
            linha["valor_campo"] = f"{linha['valor'] / 100:.2f}".replace(".", ",")
            linha["por_unidade_fmt"] = formatar(linha["por_unidade"])
        context.update(
            unidades=unidades, mostrar_unidade=len(unidades) > 1, unidade_atual=unidade, unidade_selecionada=str(unidade.pk),
            linhas=linhas, pode_ajustar=pode_ajustar, sem_historico=totais["sem_historico"],
            total_mensal=formatar(totais["mensal"]), total_distribuido=formatar(totais["distribuido"]),
            total_nao_distribuido=formatar(totais["nao_distribuido"]), excedente_manual=formatar(totais["excedente_manual"]),
            tem_nao_distribuido=bool(totais["nao_distribuido"]), tem_excedente=bool(totais["excedente_manual"]),
        )
        return context


class CustoCategoriaAjusteView(View):
    """POST: fixa (valor) ou restaura (restaurar=1) o custo operacional de UMA categoria. O escopo
    vem do usuário (unidade que ele pode ALTERAR); categoria/unidade de fora = 404."""

    def post(self, request, pk):
        escopo = clientes_dos_itens(request.user, "change")
        categoria = get_object_or_404(Categoria.objects.select_related("cliente"), pk=pk, cliente__in=escopo)
        retorno = f"{reverse('custo-categorias')}?unidade={categoria.cliente_id}"
        try:
            if request.POST.get("restaurar"):
                restaurar_categoria(request, categoria.cliente, categoria)
                messages.success(request, f'"{categoria.nome}" voltou ao cálculo automático.')
            else:
                # de_texto levanta RegraNegocioError (mensagem amigável) para texto inválido
                ajustar_categoria(request, categoria.cliente, categoria, DinheiroService.de_texto(request.POST.get("valor", "")))
                messages.success(request, f'Custo operacional de "{categoria.nome}" ajustado manualmente.')
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect(retorno)


# ---------------------------------------------------------------- Custos comuns (Configurações)
CABECALHO_COMUNS = ["Custo comum", "Tipo", "Descrição", "Origem", "Unidade", "Status"]


class CustoComumListView(ListaPaginadaMixin, ListView):
    """Custos comuns que a unidade pode usar: os PADRÕES do sistema (somente leitura) e os
    próprios da unidade (e, para a matriz com permissão, das filiais). Filtros: pesquisa por nome
    ou descrição, tipo (fixo/variável), origem (sistema/próprio) e status; ordenação por nome,
    tipo ou data; paginação e exportação."""

    model = CustoModelo
    template_name = "custo_comum.html"
    context_object_name = "comuns"
    colunas_ordenaveis = {"nome": "nome", "tipo": "tipo", "criado_em": "criado_em"}

    def clientes_do_escopo(self):
        return clientes_dos_itens(self.request.user, "view")

    def get_queryset(self):
        # Visível: padrões do sistema + custos comuns das unidades do escopo (nunca de outras).
        queryset = CustoModelo.objects.select_related("cliente").filter(Q(cliente__isnull=True) | Q(cliente__in=self.clientes_do_escopo()))
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo))
        if params.get("tipo") in (TIPO_FIXO, TIPO_VARIAVEL):
            queryset = queryset.filter(tipo=params["tipo"])
        origem = params.get("origem")
        if origem == "sistema":
            queryset = queryset.filter(cliente__isnull=True)
        elif origem == "proprio":
            queryset = queryset.filter(cliente__isnull=False)
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(request, acao="exportar", modulo="custo", descricao=f"Exportação de Custos comuns em {formato} ({queryset.count()} registro(s)).")
            linhas = [
                [c.nome, c.get_tipo_display(), c.descricao, "Padrão do sistema" if c.padrao_do_sistema else "Próprio",
                 "—" if c.padrao_do_sistema else c.cliente.nome_razao_social, "Ativo" if c.ativo else "Inativo"]
                for c in queryset
            ]
            if formato == "xlsx":
                return exportar_excel("custos-comuns", "Custos comuns", CABECALHO_COMUNS, [[sanitizar_texto_excel(c) for c in l] for l in linhas])
            filtros = ", ".join(f"{k}={v}" for k, v in request.GET.items() if v and k not in ("export", "page", "per_page"))
            return exportar_pdf("custos-comuns", "Relatório de Custos comuns", request.user, CABECALHO_COMUNS, linhas, filtros)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        # Botões só onde a operação será aceita no servidor (mesma regra de `custos_comuns_gerenciaveis`).
        admin = administrador_global(self.request.user)
        alteraveis = set(clientes_dos_itens(self.request.user, "change").values_list("pk", flat=True))
        apagaveis = set(clientes_dos_itens(self.request.user, "delete").values_list("pk", flat=True))
        for comum in context["comuns"]:
            padrao = comum.cliente_id is None
            comum.pode_editar = admin if padrao else comum.cliente_id in alteraveis
            comum.pode_excluir = admin if padrao else comum.cliente_id in apagaveis
        context.update(tipos=TIPO_CHOICES, tipo_selecionado=self.request.GET.get("tipo", ""), origem_selecionada=self.request.GET.get("origem", ""))
        return context


class CustoComumFormMixin:
    model = CustoModelo
    form_class = CustoComumForm
    template_name = "custo_comum_form.html"
    success_url = reverse_lazy("custo-comum-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        objeto = getattr(self, "object", None)
        kwargs["cliente"] = objeto.cliente if objeto is not None else self.request.user.profile.cliente
        return kwargs


class CustoComumCreateView(SalvarEAdicionarMixin, AuditoriaFormMixin, CustoComumFormMixin, CreateView):
    nome_url_novo = "custo-comum-create"

    def form_valid(self, form):
        form.instance.cliente = self.request.user.profile.cliente  # o dono vem do usuário, nunca do formulário
        response = super().form_valid(form)
        messages.success(self.request, "Custo comum cadastrado com sucesso.")
        return response


def custos_comuns_gerenciaveis(usuario, acao):
    """Custos comuns que o usuário pode alterar (acao "change") ou excluir ("delete"): os das unidades
    do seu escopo e, SÓ para o administrador global, também os padrões do sistema (`cliente` nulo),
    que valem para todas as empresas. Outras empresas nunca entram. Usado pelas telas e pela API."""
    filtro = Q(cliente__in=clientes_dos_itens(usuario, acao))
    if administrador_global(usuario):
        filtro |= Q(cliente__isnull=True)
    return CustoModelo.objects.filter(filtro)


class CustoComumUpdateView(AuditoriaFormMixin, CustoComumFormMixin, UpdateView):
    """Custos comuns próprios de unidades que o usuário pode alterar; os padrões do sistema só o
    administrador global altera. Os de outras empresas dão 404."""

    def get_queryset(self):
        return custos_comuns_gerenciaveis(self.request.user, "change")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Custo comum atualizado com sucesso.")
        return response


class CustoComumExcluirView(View):
    """Custo comum sem uso é apagado; se já foi usado em algum custo operacional, só é desativado
    (os custos e relatórios existentes continuam intactos). Escopo: ver `custos_comuns_gerenciaveis`
    (padrão do sistema só para o administrador global)."""

    def post(self, request, pk):
        with transaction.atomic():
            comum = get_object_or_404(custos_comuns_gerenciaveis(request.user, "delete").select_for_update(), pk=pk)
            nome = comum.nome
            if em_uso(comum):
                registrar_exclusao(request, comum, logica=True)
                comum.ativo = False
                comum.save(update_fields=["ativo", "atualizado_em"])
                messages.warning(request, f'O custo comum "{nome}" está em uso e não pode ser apagado; foi apenas desativado.')
            else:
                registrar_exclusao(request, comum)  # antes do delete(): depois o objeto perde o id
                comum.delete()
                messages.success(request, f'Custo comum "{nome}" excluído com sucesso.')
        return redirect("custo-comum-list")
