from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_edicao, registrar_exclusao, tirar_foto
from web_project import TemplateLayout
from web_project.regras import RegraNegocioError
from web_project.dimensoes import SIGLA_POR_DIMENSOES, texto_decimal
from web_project.regras.conversao import DIMENSOES, medida_e_metro, preco_por_unidade_padrao, quantidade_por_dimensoes, sigla_preco_dimensoes
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro

from .forms import MedidaForm
from .models import Medida

# Whitelist de colunas ordenáveis (nunca repassar "sort" da URL direto ao banco).
COLUNAS_ORDENAVEIS = {"medida": "medida", "sigla": "sigla", "dimensao": "dimensao", "criado_em": "criado_em", "id": "id"}
PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]


class MedidaListView(ListView):
    """Lista de medidas com pesquisa, filtros (dimensão e status), ordenação e paginação."""

    model = Medida
    template_name = "medida.html"
    context_object_name = "medidas"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("usuario")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(medida__icontains=termo) | Q(sigla__icontains=termo) | Q(descricao__icontains=termo))

        dimensao = self.request.GET.get("dimensao", "")
        if dimensao in DIMENSOES:
            queryset = queryset.filter(dimensao=dimensao)

        status = self.request.GET.get("status", "")
        if status == "ativo":
            queryset = queryset.filter(ativo=True)
        elif status == "inativo":
            queryset = queryset.filter(ativo=False)

        campo = COLUNAS_ORDENAVEIS.get(self.request.GET.get("sort", "id"), "id")
        if self.request.GET.get("dir", "asc") == "desc":
            campo = f"-{campo}"
        return queryset.order_by(campo)

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", PADRAO_POR_PAGINA))
        except (TypeError, ValueError):
            valor = PADRAO_POR_PAGINA
        return valor if valor in OPCOES_POR_PAGINA else PADRAO_POR_PAGINA

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            {
                "termo_pesquisa": self.request.GET.get("q", ""),
                "dimensao_filtro": self.request.GET.get("dimensao", ""),
                "status_filtro": self.request.GET.get("status", ""),
                "dimensoes": DIMENSOES,
                "per_page": self.get_paginate_by(None),
                "opcoes_por_pagina": OPCOES_POR_PAGINA,
                "sort": self.request.GET.get("sort", "id"),
                "dir": self.request.GET.get("dir", "asc"),
            }
        )
        return context


class MedidaCreateView(AuditoriaFormMixin, CreateView):
    model = Medida
    form_class = MedidaForm
    template_name = "medida_form.html"
    success_url = reverse_lazy("medida-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        form.instance.usuario = self.request.user  # quem cadastrou (coluna id_usuario)
        response = super().form_valid(form)  # salva e audita (AuditoriaFormMixin)
        messages.success(self.request, "Medida cadastrada com sucesso.")
        return response


class MedidaUpdateView(AuditoriaFormMixin, UpdateView):
    model = Medida
    form_class = MedidaForm
    template_name = "medida_form.html"
    success_url = reverse_lazy("medida-list")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["em_uso"] = self.object.em_uso()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)  # salva e audita (AuditoriaFormMixin)
        messages.success(self.request, "Medida atualizada com sucesso.")
        return response


class MedidaAlternarAtivoView(View):
    """Ativa/inativa uma medida. Medida inativa some dos formulários novos,
    mas continua valendo para registros que já a usam."""

    def post(self, request, pk):
        medida = get_object_or_404(Medida, pk=pk)
        with transaction.atomic():
            foto = tirar_foto(medida)
            medida.ativo = not medida.ativo
            medida.save(update_fields=["ativo", "atualizado_em"])
            registrar_edicao(request, medida, foto)  # grava "Ativo: Sim -> Não"
        situacao = "ativada" if medida.ativo else "inativada"
        messages.success(request, f'Medida "{medida.medida}" {situacao}.')
        return redirect("medida-list")


class MedidaExcluirView(View):
    """Exclusão segura (PROMPT_MESTRE, seção 26).

    NÃO usa CASCADE: apagar uma medida em cascata apagaria insumos (e o
    histórico de preços deles). Então:
      - medida sem nenhum registro usando -> exclusão física;
      - medida em uso -> só é INATIVADA (nada se perde).
    Tudo dentro de uma transação para nunca deixar estado parcial.
    """

    def post(self, request, pk):
        with transaction.atomic():
            medida = get_object_or_404(Medida.objects.select_for_update(), pk=pk)
            nome = medida.medida
            if medida.em_uso():
                registrar_exclusao(request, medida, logica=True)
                medida.ativo = False
                medida.save(update_fields=["ativo", "atualizado_em"])
                messages.warning(request, f'A medida "{nome}" está em uso e não pode ser apagada; ela foi apenas inativada.')
            else:
                registrar_exclusao(request, medida)  # antes do delete(): depois o objeto perde o id
                medida.delete()
                messages.success(request, f'Medida "{nome}" excluída com sucesso.')
        return redirect("medida-list")


class MedidaCalcularView(View):
    """Cálculos de apoio aos formulários (prévia na tela, sem gravar nada).

    GET /medidas/calcular?medida=<id>&quantidade=100&preco=4,00 devolve:
      - quantidade por extenso ('100 gramas');
      - o rótulo e o valor de 'PREÇO (por KG)'.
    Unidade Metro: com `dimensoes=2|3` e `comprimento`, `largura` [, `altura`] a quantidade é o produto das medidas
    (m² / m³), o rótulo vira 'PREÇO (por M²)' e `quantidade_calculada` devolve o total.
    Usa exatamente as mesmas funções do backend (web_project/regras), então
    a prévia nunca diverge do valor que será gravado. Qualquer usuário
    autenticado pode usar (não exige permissão de administrar medidas).
    """

    def get(self, request):
        resposta = {"quantidade_extenso": None, "rotulo_preco": None, "preco_unidade": None, "quantidade_calculada": None}
        medida_id = request.GET.get("medida", "")
        medida = Medida.objects.filter(pk=medida_id, ativo=True).first() if medida_id.isdigit() else None
        if medida is None:
            return JsonResponse(resposta)

        dimensoes = int(request.GET["dimensoes"]) if request.GET.get("dimensoes", "").isdigit() and medida_e_metro(medida) else 1
        resposta["rotulo_preco"] = f"PREÇO (por {sigla_preco_dimensoes(medida, dimensoes)})"
        try:
            if dimensoes > 1:
                medidas = [ler_numero_brasileiro(request.GET.get(c)) if request.GET.get(c, "").strip() else None for c in ("comprimento", "largura", "altura")]
                quantidade = quantidade_por_dimensoes(dimensoes, *medidas)
                resposta["quantidade_calculada"] = f"{texto_decimal(quantidade)} {SIGLA_POR_DIMENSOES[dimensoes]}"
                resposta["quantidade_extenso"] = resposta["quantidade_calculada"]
            else:
                quantidade = ler_numero_brasileiro(request.GET.get("quantidade"))
                resposta["quantidade_extenso"] = medida.por_extenso(quantidade)
            if quantidade > 0:
                centavos = DinheiroService.de_texto(request.GET.get("preco"))
                resposta["preco_unidade"] = DinheiroService.para_api(
                    preco_por_unidade_padrao(centavos, quantidade, medida)
                )
        except RegraNegocioError:
            pass  # entrada ainda incompleta/inválida: devolve só o que foi possível calcular
        return JsonResponse(resposta)
