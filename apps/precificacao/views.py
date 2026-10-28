from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.utils import registrar as registrar_auditoria
from apps.auditoria.utils import tirar_foto
from apps.canal.models import CanalVenda
from web_project.globais import q_globais_do_usuario
from apps.categoria.models import Categoria
from apps.custo.services import custo_operacional_da_categoria
from apps.ficha.models import Ficha
from apps.produto.models import Produto
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import (
    ClienteScopedMixin,
    ExcluirOuDesativarView,
    ListaPaginadaMixin,
    ListaPorUnidadeMixin,
    SalvarEAdicionarMixin,
)
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .calculos import calcular_preco
from .forms import PrecificacaoForm
from .models import Precificacao
from .services import salvar_precificacao

CABECALHO_EXPORTACAO = [
    "Produto", "Ficha", "Canal", "Unidade", "Custo da ficha", "Custo operacional", "Custo total",
    "Taxas do canal", "Método do lucro", "Lucro", "Preço de venda", "CMV (%)", "Status",
]


def _percentual(valor):
    return f"{valor:.2f}".replace(".", ",") + "%"


def _taxas_texto(p):
    partes = [_percentual(p.taxa_percentual)] if p.taxa_percentual else []
    if p.taxa_fixa:
        partes.append(DinheiroService.formatar(p.taxa_fixa))
    return " + ".join(partes) or "Sem taxas"


def _metodo_texto(metodo, percentual, valor):
    """'Margem: 30,00%', 'Markup: 50,00%' ou 'Valor: R$ 5,00'."""
    if metodo == Precificacao.METODO_VALOR:
        return f"Valor: {DinheiroService.formatar(valor)}"
    return f"{'Margem' if metodo == Precificacao.METODO_MARGEM else 'Markup'}: {_percentual(percentual)}"


class PrecificacaoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Precificações: filtros (pesquisa, produto, canal, método, status,
    unidade), ordenação, paginação e exportação Excel/PDF sobre o escopo de
    unidades do usuário."""

    model = Precificacao
    template_name = "precificacao.html"
    context_object_name = "precificacoes"
    colunas_ordenaveis = {
        "ficha": "ficha__nome", "produto": "produto__nome", "canal": "canal_nome", "preco": "preco_venda",
        "lucro": "lucro", "cmv": "cmv_percentual", "criado_em": "criado_em",
    }
    ordenacao_padrao = "produto"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("produto", "ficha")
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(ficha__nome__icontains=termo) | Q(produto__nome__icontains=termo) | Q(canal_nome__icontains=termo))
        if params.get("categoria", "").isdigit():
            queryset = queryset.filter(produto__categoria_id=params["categoria"])
        if params.get("produto", "").isdigit():
            queryset = queryset.filter(produto_id=params["produto"])
        if params.get("canal", "").isdigit():
            queryset = queryset.filter(canal_id=params["canal"])
        if params.get("metodo") in dict(Precificacao.METODO_CHOICES):
            queryset = queryset.filter(metodo_lucro=params["metodo"])
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(
                request, acao="exportar", modulo="precificacao",
                descricao=f"Exportação de Precificações em {formato} ({queryset.count()} registro(s)).",
            )
            return self.exportar(formato, queryset)
        return super().get(request, *args, **kwargs)

    def exportar(self, formato, queryset):
        linhas = [
            [
                p.produto.nome, p.ficha.nome, p.nome_do_canal, p.cliente.nome_razao_social, p.custo_ficha_formatado,
                p.custo_operacional_formatado, p.custo_total_formatado, _taxas_texto(p),
                _metodo_texto(p.metodo_lucro, p.percentual_lucro, p.valor_lucro), p.lucro_formatado,
                p.preco_venda_formatado, _percentual(p.cmv_percentual), "Ativa" if p.ativo else "Inativa",
            ]
            for p in queryset.select_related("cliente")
        ]
        if formato == "xlsx":
            return exportar_excel("precificacoes", "Precificações", CABECALHO_EXPORTACAO, [[sanitizar_texto_excel(c) for c in l] for l in linhas])
        filtros = ", ".join(f"{k}={v}" for k, v in self.request.GET.items() if v and k not in ("export", "page", "per_page"))
        return exportar_pdf("precificacoes", "Relatório de Precificações", self.request.user, CABECALHO_EXPORTACAO, linhas, filtros)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        escopo = self.clientes_do_escopo()
        context.update(
            # Formato da lista: tabela (padrão) ou cartões; vem de lista fechada. O navegador lembra a
            # escolha (static/js/precificacao-lista.js) e a repete na URL, então filtros e páginas a mantêm.
            visao=self.request.GET.get("visao") if self.request.GET.get("visao") in ("tabela", "cartoes") else "tabela",
            produtos=Produto.objects.filter(cliente__in=escopo).order_by("nome"),
            canais=CanalVenda.objects.filter(Q(cliente__in=escopo) | q_globais_do_usuario(CanalVenda, self.request.user)).distinct().order_by("nome", "descricao"),
            metodos=Precificacao.METODO_CHOICES,
            produto_selecionado=self.request.GET.get("produto", ""),
            categorias=Categoria.objects.filter(cliente__in=escopo).order_by("nome"),
            categoria_selecionada=self.request.GET.get("categoria", ""),
            canal_selecionado=self.request.GET.get("canal", ""),
            metodo_selecionado=self.request.GET.get("metodo", ""),
        )
        return context


def resumo_da_ficha(ficha):
    """Linhas e subtotais da ficha técnica para a tela: insumos + mão de obra opcional. O custo da ficha (`custo_total`) já soma
    os dois, calculado no servidor (apps/ficha/services.py); aqui só se mostra de onde ele vem, sem recalcular nada no cliente."""
    if ficha is None:
        return {"itens": [], "mao_de_obra": [], "subtotal_insumos": None, "subtotal_mao_de_obra": None}
    itens = list(ficha.itens.select_related("insumo", "medida"))
    mao_de_obra = list(ficha.mao_de_obra.select_related("colaborador"))
    return {
        "itens": itens, "mao_de_obra": mao_de_obra,
        "subtotal_insumos": DinheiroService.formatar(sum(i.custo for i in itens)),
        "subtotal_mao_de_obra": DinheiroService.formatar(sum(m.custo for m in mao_de_obra)),
    }


class PrecificacaoFormMixin:
    model = Precificacao
    form_class = PrecificacaoForm
    template_name = "precificacao_form.html"
    success_url = reverse_lazy("precificacao")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        ficha = getattr(self.object, "ficha", None) if self.object is not None else None
        context["ficha_atual"] = ficha
        resumo = resumo_da_ficha(ficha)
        context["itens_ficha"] = resumo["itens"]
        context["mao_de_obra_ficha"] = resumo["mao_de_obra"]
        context["subtotal_insumos"] = resumo["subtotal_insumos"]
        context["subtotal_mao_de_obra"] = resumo["subtotal_mao_de_obra"]
        return context

    def form_valid(self, form):
        # O erro de cálculo (ex.: margem + taxas ≥ 100 %) volta como erro do formulário.
        form.instance.cliente = form.instance.cliente if form.instance.pk else self.request.user.profile.cliente
        try:
            self.object = salvar_precificacao(self.request, form.instance, foto_antes=getattr(self, "_foto_antes", None))
        except ValidationError as erro:
            form.add_error(None, erro)
            return self.form_invalid(form)
        messages.success(self.request, self.mensagem_sucesso)
        return redirect(self.get_success_url())  # em "Salvar e adicionar outro", volta ao formulário em branco


class PrecificacaoCreateView(PrecificacaoFormMixin, SalvarEAdicionarMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    nome_url_novo = "precificacao-create"
    mensagem_sucesso = "Precificação cadastrada com sucesso."


class PrecificacaoUpdateView(PrecificacaoFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"
    mensagem_sucesso = "Precificação atualizada com sucesso."

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)  # antes de o formulário alterar o objeto
        return objeto


class PrecificacaoExcluirView(ExcluirOuDesativarView):
    """Precificação não tem dependentes: é apagada (a auditoria registra)."""

    model = Precificacao
    url_lista = "precificacao"
    rotulo = "precificação"
    artigo = "a"


class PrecificacaoCalcularView(View):
    """Prévia (sem gravar) do formulário: itens da ficha com custo e toda a
    decomposição até o preço de venda, calculados por `calcular_preco` (a mesma
    função da gravação). O navegador apenas exibe o que o servidor devolve.

    POST com os campos do formulário (CSRF obrigatório). Fichas/canais só do
    escopo do usuário; o formulário valida; se houver erro, devolve `erros` e,
    quando a ficha é válida, ao menos os itens dela."""

    def post(self, request):
        cliente = request.user.profile.cliente
        form = PrecificacaoForm(request.POST, cliente=cliente)
        # A prévia precisa enxergar também as fichas/canais de filiais que o usuário pode ver (edição).
        escopo = clientes_dos_itens(request.user, "view")
        form.fields["ficha"].queryset = Ficha.objects.filter(cliente__in=escopo, ativo=True).select_related("produto")
        form.fields["canal"].queryset = CanalVenda.objects.filter(Q(cliente__in=escopo) | q_globais_do_usuario(CanalVenda, request.user), ativo=True).distinct()
        valido = form.is_valid()
        resposta = {"erros": {} if valido else {k: v[0] for k, v in form.errors.items()}, "itens": [], "mao_de_obra": [], "resultado": None}
        ficha = form.cleaned_data.get("ficha") if hasattr(form, "cleaned_data") else None
        if ficha is not None:
            # Sugestão do módulo de Custo operacional: custo por unidade da categoria do produto.
            sugestao = custo_operacional_da_categoria(ficha.produto.categoria)
            resposta["sugestao_custo_operacional"] = {
                "categoria": ficha.produto.categoria.nome, "aviso": sugestao["aviso"],
                "formatado": DinheiroService.formatar(sugestao["centavos"]),
                "valor": DinheiroService.formatar(sugestao["centavos"]).replace("R$ ", ""),
                # Resumo "Custo operacional por categoria": total da categoria no mês e se foi ajustado à mão.
                "total_categoria": DinheiroService.formatar(sugestao["total"]), "ajuste_na_categoria": sugestao["manual"],
            }
            resposta["custo_ficha"] = DinheiroService.formatar(ficha.custo_total)
            resumo = resumo_da_ficha(ficha)
            resposta["itens"] = [
                {"insumo": i.insumo.nome, "medida": i.medida.sigla, "quantidade": f"{i.quantidade.normalize():f}", "quantidade_texto": i.quantidade_texto, "custo": i.custo_formatado}
                for i in resumo["itens"]
            ]
            # Mão de obra opcional da ficha (segmentos de serviço): linhas e subtotais, para a soma da tela fechar com o custo total.
            resposta["mao_de_obra"] = [{"colaborador": m.colaborador.nome, "horas": f"{m.horas.normalize():f}", "custo": m.custo_formatado} for m in resumo["mao_de_obra"]]
            resposta["subtotal_insumos"] = resumo["subtotal_insumos"]
            resposta["subtotal_mao_de_obra"] = resumo["subtotal_mao_de_obra"]
        if valido:
            dados = form.cleaned_data
            canal = dados.get("canal")
            try:
                r = calcular_preco(
                    ficha.custo_total, dados["custo_operacional"],
                    canal.taxa_percentual_total if canal else dados["taxa_percentual"],
                    canal.taxa_fixa_total if canal else dados["taxa_fixa"],
                    dados["metodo_lucro"], dados["percentual_lucro"], dados["valor_lucro"],
                )
            except RegraNegocioError as erro:
                resposta["erros"] = {"__all__": str(erro)}
            else:
                f = DinheiroService.formatar
                resposta["resultado"] = {
                    "canal": (str(canal) if canal else (dados["canal_nome"] + (f" | {dados['canal_descricao']}" if dados["canal_descricao"] else ""))),
                    "custo_operacional": f(r.custo_operacional), "custo_ficha": f(r.custo_ficha), "custo_total": f(r.custo_total),
                    "taxas": (_percentual(r.taxa_percentual) + (f" ({f(r.taxas_percentuais_valor)})" if r.taxa_percentual else "")
                              + (f" + {f(r.taxa_fixa)}" if r.taxa_fixa else "")) if (r.taxa_percentual or r.taxa_fixa) else "Sem taxas",
                    "taxas_valor": f(r.taxas_percentuais_valor + r.taxa_fixa),
                    "metodo": _metodo_texto(dados["metodo_lucro"], dados["percentual_lucro"], dados["valor_lucro"]),
                    "lucro": f(r.lucro), "preco_venda": f(r.preco_venda), "cmv": _percentual(r.cmv_percentual), "cmv_valor": f(r.custo_ficha),
                }
        return JsonResponse(resposta)
