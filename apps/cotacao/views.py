from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.decorators import modulo_habilitado_para
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .exports import exportar_cotacao
from .forms import ContaPagarForm, CotacaoForm, ImportarListaForm, ItemForm, LojaForm, PrecoForm
from .models import Cotacao, CotacaoItem, CotacaoLoja, CotacaoPreco
from .services import (
    comparativo, escolher_preco, excluir_cotacao, excluir_filho, gerar_contas_a_pagar, importar_da_lista, limpar_escolha,
    mudar_status, resumo_escolhidos, salvar_filho,
)


def obter_cotacao(request, pk, acao):
    """Cotação dentro do escopo do usuário para a ação (view/change/delete); fora dele = 404 (sem vazar existência)."""
    return get_object_or_404(Cotacao.objects.select_related("cliente"), pk=pk, cliente__in=clientes_dos_itens(request.user, acao))


def _voltar(cotacao):
    return redirect("cotacao-detail", pk=cotacao.pk)


class CotacaoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    model = Cotacao
    template_name = "cotacao.html"
    context_object_name = "cotacoes"
    colunas_ordenaveis = {"titulo": "titulo", "criado_em": "criado_em", "status": "status"}
    ordenacao_padrao = "criado_em"
    direcao_padrao = "desc"

    def get_queryset(self):
        queryset = super().get_queryset().annotate(qtd_itens=Count("itens", distinct=True), qtd_lojas=Count("lojas", distinct=True))
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(titulo__icontains=termo) | Q(lojas__nome__icontains=termo)).distinct()
        status = self.request.GET.get("status")
        if status in dict(Cotacao.STATUS_CHOICES):
            queryset = queryset.filter(status=status)
        return self.ordenar(queryset)  # cotação não tem "ativo": o filtro de status é o próprio status

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["status_opcoes"] = Cotacao.STATUS_CHOICES
        return context


class CotacaoCreateView(AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model = Cotacao
    form_class = CotacaoForm
    template_name = "cotacao_form.html"
    acao_escopo = "add"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        form.instance.criado_por = self.request.user
        with transaction.atomic():
            resposta = super().form_valid(form)
            if self.request.POST.get("importar_lista"):
                criados = importar_da_lista(self.request, self.object)
                messages.info(self.request, f"{len(criados)} item(ns) copiado(s) da lista de compras.")
        messages.success(self.request, "Cotação criada. Inclua as lojas e os preços.")
        return resposta

    def get_success_url(self):
        return reverse("cotacao-detail", kwargs={"pk": self.object.pk})


class CotacaoUpdateView(AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model = Cotacao
    form_class = CotacaoForm
    template_name = "cotacao_form.html"
    acao_escopo = "change"

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def get_queryset(self):
        return super().get_queryset().filter(status__in=[Cotacao.RASCUNHO, Cotacao.ENVIADA])

    def get_success_url(self):
        messages.success(self.request, "Cotação atualizada.")
        return reverse("cotacao-detail", kwargs={"pk": self.object.pk})


class CotacaoDetailView(TemplateView):
    template_name = "cotacao_detail.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        cotacao = obter_cotacao(self.request, kwargs["pk"], "view")
        dados = comparativo(cotacao)
        blocos, total = resumo_escolhidos(dados)
        alteraveis = clientes_dos_itens(self.request.user, "change").values_list("pk", flat=True)
        context.update(
            cotacao=cotacao, lojas=cotacao.lojas.select_related("conta_pagar"), comparativo=dados, resumo=blocos, total_escolhidos=total, total_formatado=DinheiroService.formatar(total),
            pode_alterar=cotacao.cliente_id in alteraveis and self.request.user.has_perm("cotacao.change_cotacao"),
            pode_gerar_contas=self.request.user.has_perm("financeiro.add_contapagar") and modulo_habilitado_para(self.request.user, "financeiro-pagar"),
        )
        return context


class CotacaoExcluirView(View):
    def post(self, request, pk):
        cotacao = obter_cotacao(request, pk, "delete")
        try:
            excluir_cotacao(request, cotacao)
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
            return _voltar(cotacao)
        messages.success(request, f'Cotação "{cotacao.titulo}" excluída.')
        return redirect("cotacao")


class StatusView(View):
    """POST `acao`: enviar | fechar | reabrir | cancelar."""

    def post(self, request, pk):
        cotacao = obter_cotacao(request, pk, "change")
        try:
            mudar_status(request, cotacao, request.POST.get("acao", ""))
            messages.success(request, "Status da cotação atualizado.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return _voltar(cotacao)


class ExportarView(View):
    def get(self, request, pk):
        cotacao = obter_cotacao(request, pk, "view")
        formato = request.GET.get("formato", "pdf")
        if formato not in ("pdf", "xlsx"):
            raise Http404("Formato inválido.")
        return exportar_cotacao(request, cotacao, formato)


class ImportarListaView(View):
    """Copia itens da lista de compras da unidade da cotação (escolha por caixas de seleção). Idempotente."""

    template_name = "cotacao_importar.html"

    def disponiveis(self, cotacao):
        from apps.compras.models import ItemCompra

        ja_insumos = cotacao.itens.filter(insumo__isnull=False).values_list("insumo_id", flat=True)
        ja_texto = cotacao.itens.filter(insumo__isnull=True).values_list("descricao", flat=True)
        return ItemCompra.objects.filter(cliente_id=cotacao.cliente_id).exclude(insumo_id__in=ja_insumos).exclude(insumo__isnull=True, descricao__in=ja_texto).select_related("medida")

    def get(self, request, pk):
        cotacao = obter_cotacao(request, pk, "change")
        form = ImportarListaForm(disponiveis=self.disponiveis(cotacao))
        return render(request, self.template_name, TemplateLayout.init(self, {"cotacao": cotacao, "form": form}))

    def post(self, request, pk):
        cotacao = obter_cotacao(request, pk, "change")
        disponiveis = list(self.disponiveis(cotacao))
        form = ImportarListaForm(request.POST, disponiveis=disponiveis)
        if not form.is_valid():
            return render(request, self.template_name, TemplateLayout.init(self, {"cotacao": cotacao, "form": form}))
        ids = [int(i) for i in form.cleaned_data["itens"]]
        try:
            criados = importar_da_lista(request, cotacao, ids) if ids else []
            messages.success(request, f"{len(criados)} item(ns) copiado(s) da lista de compras.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return _voltar(cotacao)


class FilhoFormView(View):
    """Criar/editar loja, item ou preço de uma cotação (um formulário por vez). O pai é sempre buscado no escopo de
    alteração do usuário; ids de outra cotação/empresa dão 404. Gravação e auditoria em services.salvar_filho."""

    template_name = "cotacao_filho_form.html"
    form_class = None
    titulo = ""

    def contexto_pai(self, request, kwargs):
        """(cotacao, kwargs para o formulário, instância ou None). Implementado em cada subclasse."""
        raise NotImplementedError

    def render_form(self, request, form, cotacao):
        return render(request, self.template_name, TemplateLayout.init(self, {"form": form, "cotacao": cotacao, "titulo_form": self.titulo, "form_extra": self.__class__.__name__}))

    def get(self, request, **kwargs):
        cotacao, extra, instancia = self.contexto_pai(request, kwargs)
        return self.render_form(request, self.form_class(instance=instancia, **extra), cotacao)

    def post(self, request, **kwargs):
        cotacao, extra, instancia = self.contexto_pai(request, kwargs)
        form = self.form_class(request.POST, instance=instancia, **extra)
        if form.is_valid():
            try:
                salvar_filho(request, form)
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, "Salvo com sucesso.")
                return _voltar(cotacao)
        return self.render_form(request, form, cotacao)


class LojaFormView(FilhoFormView):
    form_class = LojaForm
    titulo = "Loja"

    def contexto_pai(self, request, kwargs):
        cotacao = obter_cotacao(request, kwargs["pk"], "change")
        instancia = get_object_or_404(CotacaoLoja, pk=kwargs["loja"], cotacao=cotacao) if "loja" in kwargs else None
        return cotacao, {"cotacao": cotacao}, instancia


class ItemFormView(FilhoFormView):
    form_class = ItemForm
    titulo = "Item"

    def contexto_pai(self, request, kwargs):
        cotacao = obter_cotacao(request, kwargs["pk"], "change")
        instancia = get_object_or_404(CotacaoItem, pk=kwargs["item"], cotacao=cotacao) if "item" in kwargs else None
        return cotacao, {"cotacao": cotacao}, instancia


class PrecoFormView(FilhoFormView):
    form_class = PrecoForm
    titulo = "Preço de uma loja"

    def contexto_pai(self, request, kwargs):
        cotacao = obter_cotacao(request, kwargs["pk"], "change")
        if "preco" in kwargs:
            instancia = get_object_or_404(CotacaoPreco.objects.select_related("item", "loja"), pk=kwargs["preco"], item__cotacao=cotacao)
            return cotacao, {"item": instancia.item}, instancia
        item = get_object_or_404(CotacaoItem, pk=kwargs["item"], cotacao=cotacao)
        return cotacao, {"item": item}, None

    def get(self, request, **kwargs):
        cotacao, extra, instancia = self.contexto_pai(request, kwargs)
        inicial = {"quantidade": extra["item"].quantidade, "medida": extra["item"].medida_id}
        if instancia is None and request.GET.get("loja", "").isdigit():
            inicial["loja"] = request.GET["loja"]
        return self.render_form(request, self.form_class(instance=instancia, initial=inicial if instancia is None else None, **extra), cotacao)


class ExcluirFilhoView(View):
    """POST: remove loja, item ou preço da cotação (escopo de alteração; auditado)."""

    modelo = None
    campo_pk = None

    def post(self, request, pk, **kwargs):
        cotacao = obter_cotacao(request, pk, "change")
        consulta = self.modelo.objects.all()
        filtro = {"pk": kwargs[self.campo_pk], ("item__cotacao" if self.modelo is CotacaoPreco else "cotacao"): cotacao}
        objeto = get_object_or_404(consulta, **filtro)
        try:
            excluir_filho(request, objeto)
            messages.success(request, "Removido.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return _voltar(cotacao)


class EscolherView(View):
    """POST: marca o preço como vencedor do item. POST em `limpar-escolha`: remove o vencedor do item."""

    def post(self, request, pk, preco):
        cotacao = obter_cotacao(request, pk, "change")
        oferta = get_object_or_404(CotacaoPreco.objects.select_related("item", "loja", "medida"), pk=preco, item__cotacao=cotacao)
        try:
            escolher_preco(request, oferta)
            messages.success(request, f"{oferta.loja.nome} escolhida para {oferta.item.descricao}.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return _voltar(cotacao)


class LimparEscolhaView(View):
    def post(self, request, pk, item):
        cotacao = obter_cotacao(request, pk, "change")
        try:
            limpar_escolha(request, get_object_or_404(CotacaoItem.objects.select_related("cotacao"), pk=item, cotacao=cotacao))
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return _voltar(cotacao)


class GerarContasView(View):
    """Gera as contas a pagar dos vencedores (uma por loja). Exige cotação fechada, permissão de criar conta a pagar e o
    módulo financeiro habilitado; a categoria deve ser de despesa da unidade (validada pelo formulário de contas)."""

    template_name = "cotacao_contas.html"

    def categorias(self, cotacao):
        from apps.financeiro.models import TIPO_DESPESA, CategoriaFinanceira

        return CategoriaFinanceira.objects.filter(cliente_id=cotacao.cliente_id, tipo=TIPO_DESPESA, ativo=True).order_by("nome")

    def liberado(self, request):
        return request.user.has_perm("financeiro.add_contapagar") and modulo_habilitado_para(request.user, "financeiro-pagar")

    def get(self, request, pk):
        cotacao = obter_cotacao(request, pk, "change")
        if not self.liberado(request):
            raise Http404
        _, total = resumo_escolhidos(comparativo(cotacao))
        return render(request, self.template_name, TemplateLayout.init(self, {"cotacao": cotacao, "form": ContaPagarForm(categorias=self.categorias(cotacao)), "total": total}))

    def post(self, request, pk):
        cotacao = obter_cotacao(request, pk, "change")
        if not self.liberado(request):
            raise Http404
        form = ContaPagarForm(request.POST, categorias=self.categorias(cotacao))
        if form.is_valid():
            try:
                contas = gerar_contas_a_pagar(request, cotacao, form.cleaned_data["categoria"], form.cleaned_data["vencimento"])
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, f"{len(contas)} conta(s) a pagar gerada(s)." if contas else "Nenhuma conta nova: as lojas vencedoras já têm conta.")
                return _voltar(cotacao)
        _, total = resumo_escolhidos(comparativo(cotacao))
        return render(request, self.template_name, TemplateLayout.init(self, {"cotacao": cotacao, "form": form, "total": total}))
