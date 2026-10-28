import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.utils import registrar as registrar_auditoria
from apps.auditoria.utils import tirar_foto
from apps.categoria.models import Categoria
from apps.insumo.models import Insumo
from apps.medida.models import Medida
from apps.processado.recalculo import custo_do_item
from apps.produto.models import Produto
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.dimensoes import SIGLA_POR_DIMENSOES, dimensoes_de, texto_decimal
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.mixins import ClienteScopedMixin, ExcluirOuDesativarView, ListaPaginadaMixin, ListaPorUnidadeMixin
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import medida_e_metro, quantidade_por_dimensoes
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro

from apps.maodeobra.models import Colaborador, segmento_permite

from .forms import FichaForm, ItemFormSet, MaoDeObraFormSet
from .models import Ficha, FichaItem, FichaMaoDeObra
from .services import custo_da_mao_de_obra, salvar_ficha

CABECALHO_EXPORTACAO = ["Categoria", "Produto", "Unidade", "Itens", "Custo total", "Status", "Cadastrado em"]


class FichaListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Fichas: filtros (pesquisa, produto, status, unidade), ordenação,
    paginação e exportação Excel/PDF sobre o escopo de unidades do usuário."""

    model = Ficha
    template_name = "ficha.html"
    context_object_name = "fichas"
    colunas_ordenaveis = {"nome": "nome", "categoria": "produto__categoria__nome", "produto": "produto__nome", "custo": "custo_total", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().select_related("produto", "produto__categoria")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(produto__nome__icontains=termo) | Q(produto__categoria__nome__icontains=termo))
        categoria = self.request.GET.get("categoria", "")
        if categoria.isdigit():
            queryset = queryset.filter(produto__categoria_id=categoria)
        produto = self.request.GET.get("produto", "")
        if produto.isdigit():
            queryset = queryset.filter(produto_id=produto)
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        formato = request.GET.get("export")
        if formato in ("xlsx", "pdf"):
            queryset = self.get_queryset()
            registrar_auditoria(
                request, acao="exportar", modulo="ficha",
                descricao=f"Exportação de Fichas técnicas em {formato} ({queryset.count()} registro(s)).",
            )
            return self.exportar(formato, queryset)
        return super().get(request, *args, **kwargs)

    def exportar(self, formato, queryset):
        linhas = [
            [
                f.produto.categoria.nome, f.produto.nome, f.cliente.nome_razao_social, f.qtd_itens, f.custo_total_formatado,
                "Ativo" if f.ativo else "Inativo", timezone.localtime(f.criado_em).strftime("%d/%m/%Y %H:%M"),
            ]
            for f in queryset.select_related("cliente", "produto__categoria").annotate(qtd_itens=Count("itens"))
        ]
        if formato == "xlsx":
            return exportar_excel("fichas-tecnicas", "Fichas técnicas", CABECALHO_EXPORTACAO,
                                  [[sanitizar_texto_excel(c) if isinstance(c, str) else c for c in l] for l in linhas])
        filtros = ", ".join(f"{k}={v}" for k, v in self.request.GET.items() if v and k not in ("export", "page", "per_page"))
        return exportar_pdf("fichas-tecnicas", "Relatório de Fichas técnicas", self.request.user, CABECALHO_EXPORTACAO, linhas, filtros)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["produtos"] = Produto.objects.filter(cliente__in=self.clientes_do_escopo()).order_by("nome")
        context["produto_selecionado"] = self.request.GET.get("produto", "")
        context["categorias"] = Categoria.objects.filter(cliente__in=self.clientes_do_escopo()).order_by("nome")
        context["categoria_selecionada"] = self.request.GET.get("categoria", "")
        return context


class FichaFormMixin:
    """Ficha + formset de itens gravados juntos por services.salvar_ficha."""

    model = Ficha
    form_class = FichaForm
    template_name = "ficha_form.html"
    success_url = reverse_lazy("ficha")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["ver_receita"] = self.request.user.has_perm("ficha.ver_receita")  # receita só com a permissão
        return kwargs

    def _instancia(self):
        return self.object if self.object is not None else Ficha(cliente=self.request.user.profile.cliente)

    def usa_mao_de_obra(self):
        """Mão de obra na ficha só para segmentos de Serviço e de Produto e Serviço (a mesma regra do módulo Mão de obra)."""
        return segmento_permite(self._instancia().cliente)

    def get_formset(self):
        dados = self.request.POST if self.request.method == "POST" else None
        instancia = self._instancia()
        # Nesses mesmos segmentos os insumos deixam de ser obrigatórios.
        return ItemFormSet(dados, instance=instancia, cliente=instancia.cliente, itens_opcionais=self.usa_mao_de_obra())

    def get_mao_de_obra_formset(self):
        if not self.usa_mao_de_obra():
            return None
        dados = self.request.POST if self.request.method == "POST" else None
        instancia = self._instancia()
        return MaoDeObraFormSet(dados, instance=instancia, cliente=instancia.cliente)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.setdefault("formset", self.get_formset())
        context.setdefault("mo_formset", self.get_mao_de_obra_formset())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if isinstance(self, UpdateView) else None
        form = self.get_form()
        formset = self.get_formset()
        mo_formset = self.get_mao_de_obra_formset()
        if form.is_valid() and formset.is_valid() and (mo_formset is None or mo_formset.is_valid()):
            return self.form_valid(form, formset, mo_formset)
        return self.render_to_response(self.get_context_data(form=form, formset=formset, mo_formset=mo_formset))

    def form_valid(self, form, formset, mo_formset=None):
        ficha = form.instance
        if ficha.pk is None:  # só ao criar: o dono é a unidade do usuário
            ficha.cliente = self.request.user.profile.cliente
        itens = [
            FichaItem(insumo=f.cleaned_data["insumo"], medida=f.cleaned_data["medida"], quantidade=f.cleaned_data["quantidade"], **dimensoes_de(f))
            for f in formset.itens_validos()
        ]
        mao_de_obra = None if mo_formset is None else [
            FichaMaoDeObra(colaborador=f.cleaned_data["colaborador"], horas=f.cleaned_data["horas"]) for f in mo_formset.linhas_validas()
        ]
        try:
            salvar_ficha(self.request, ficha, itens, foto_antes=getattr(self, "_foto_antes", None), mao_de_obra=mao_de_obra)
        except ValidationError as erro:
            form.add_error(None, erro)
            return self.render_to_response(self.get_context_data(form=form, formset=formset, mo_formset=mo_formset))
        messages.success(self.request, self.mensagem_sucesso)
        if "salvar_e_novo" in self.request.POST and self.object is None:
            return redirect("ficha-create")
        return redirect(self.success_url)


class FichaCreateView(FichaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    mensagem_sucesso = "Ficha técnica cadastrada com sucesso."


class FichaUpdateView(FichaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"
    mensagem_sucesso = "Ficha técnica atualizada com sucesso."

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)  # antes de o formulário alterar o objeto
        return objeto


class FichaExcluirView(ExcluirOuDesativarView):
    """Sem uso (precificações) -> apagada; em uso -> só desativada."""

    model = Ficha
    url_lista = "ficha"
    rotulo = "ficha"
    artigo = "a"


class FichaCalcularView(View):
    """Prévia (sem gravar) do custo de cada linha e do total enquanto o
    formulário é preenchido. Usa `custo_do_item`, a mesma função da gravação:
    o navegador só exibe o que o servidor devolve.

    POST JSON {"itens": [{"insumo": 1, "medida": 2, "quantidade": "1,5"}], "mao_de_obra": [{"colaborador": 3, "horas": "2"}]}
    -> {"itens": [{"custo": "R$ 1,00", "extenso": "1,5 quilos", "erro": null}], "mao_de_obra": [{"custo": "R$ 40,00", "erro": null}],
        "total": "R$ 41,00"}   ("mao_de_obra" é opcional nos dois sentidos; o processado não a usa)
    Unidade Metro: a linha pode trazer "dimensoes" (1, 2 ou 3) e "comprimento"/"largura"/"altura"; com 2 ou 3 a quantidade
    é o produto das medidas (web_project/regras/conversao.py::quantidade_por_dimensoes) e o campo "quantidade" é ignorado.
    Insumos e colaboradores só dentro do escopo de visualização do usuário (tenant)."""

    def post(self, request):
        try:
            payload = json.loads(request.body or b"{}")
            linhas = payload.get("itens", [])
        except (ValueError, AttributeError):
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        if not isinstance(linhas, list) or len(linhas) > 200:
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        ids_insumo = {int(l["insumo"]) for l in linhas if isinstance(l, dict) and str(l.get("insumo", "")).isdigit()}
        ids_medida = {int(l["medida"]) for l in linhas if isinstance(l, dict) and str(l.get("medida", "")).isdigit()}
        insumos = {
            i.pk: i for i in Insumo.objects.select_related("medida").filter(
                pk__in=ids_insumo, cliente__in=clientes_dos_itens(request.user, "view"))
        }
        medidas = {m.pk: m for m in Medida.objects.filter(pk__in=ids_medida, ativo=True)}
        resultado, total = [], 0
        for linha in linhas:
            item = {"custo": "—", "extenso": "", "erro": None}
            try:
                insumo = insumos.get(int(linha.get("insumo") or 0))
                medida = medidas.get(int(linha.get("medida") or 0))
                dimensoes = int(linha.get("dimensoes") or 1) if medida is not None and medida_e_metro(medida) else 1
                quantidade = Decimal(0)
                if medida is not None and dimensoes > 1:
                    # metro em área/volume: a quantidade é o produto das medidas (a mesma função da gravação)
                    lados = (ler_numero_brasileiro(linha[c]) if str(linha.get(c) or "").strip() else None for c in ("comprimento", "largura", "altura"))
                    quantidade = quantidade_por_dimensoes(dimensoes, *lados)
                    item["extenso"] = f"{texto_decimal(quantidade)} {SIGLA_POR_DIMENSOES[dimensoes]}"
                elif medida is not None and str(linha.get("quantidade", "")).strip():
                    quantidade = ler_numero_brasileiro(linha["quantidade"])
                    if quantidade > 0:
                        item["extenso"] = medida.por_extenso(quantidade)  # "2 unidades", "100 gramas"
                if insumo and medida is not None and quantidade > 0:
                    custo = custo_do_item(insumo, medida, quantidade, dimensoes)
                    total += custo
                    item["custo"] = DinheiroService.formatar(custo)
            except (RegraNegocioError, ValueError, TypeError, AttributeError) as erro:
                item["erro"] = str(erro) if isinstance(erro, RegraNegocioError) else "Valores inválidos."
            resultado.append(item)
        mao_de_obra = []
        brutas = payload.get("mao_de_obra", []) if isinstance(payload, dict) else []
        if isinstance(brutas, list) and len(brutas) <= 200:
            ids = {int(l["colaborador"]) for l in brutas if isinstance(l, dict) and str(l.get("colaborador", "")).isdigit()}
            colaboradores = {c.pk: c for c in Colaborador.objects.filter(pk__in=ids, cliente__in=clientes_dos_itens(request.user, "view"))}
            for linha in brutas:
                saida = {"custo": "—", "erro": None}
                try:
                    colaborador = colaboradores.get(int(linha.get("colaborador") or 0))
                    if colaborador and str(linha.get("horas", "")).strip():
                        horas = ler_numero_brasileiro(linha["horas"])
                        if horas > 0:
                            custo = custo_da_mao_de_obra(colaborador, horas)
                            total += custo
                            saida["custo"] = DinheiroService.formatar(custo)
                except (RegraNegocioError, ValueError, TypeError, AttributeError) as erro:
                    saida["erro"] = str(erro) if isinstance(erro, RegraNegocioError) else "Valores inválidos."
                mao_de_obra.append(saida)
        return JsonResponse({"itens": resultado, "mao_de_obra": mao_de_obra, "total": DinheiroService.formatar(total)})
