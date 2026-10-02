from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.utils import registrar as registrar_auditoria
from web_project import TemplateLayout
from web_project.regras.conversao import DIMENSOES

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


class MedidaCreateView(CreateView):
    model = Medida
    form_class = MedidaForm
    template_name = "medida_form.html"
    success_url = reverse_lazy("medida-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        form.instance.usuario = self.request.user  # quem cadastrou (coluna id_usuario)
        response = super().form_valid(form)
        registrar_auditoria(
            self.request, acao="criar", modulo="medida", registro_id=self.object.pk,
            descricao=f'Criada medida ID {self.object.pk} - {self.object.medida}.',
        )
        messages.success(self.request, "Medida cadastrada com sucesso.")
        return response


class MedidaUpdateView(UpdateView):
    model = Medida
    form_class = MedidaForm
    template_name = "medida_form.html"
    success_url = reverse_lazy("medida-list")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["em_uso"] = self.object.em_uso()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        registrar_auditoria(
            self.request, acao="editar", modulo="medida", registro_id=self.object.pk,
            descricao=f'Atualizada medida ID {self.object.pk} - {self.object.medida}.',
        )
        messages.success(self.request, "Medida atualizada com sucesso.")
        return response


class MedidaAlternarAtivoView(View):
    """Ativa/inativa uma medida. Medida inativa some dos formulários novos,
    mas continua valendo para registros que já a usam."""

    def post(self, request, pk):
        medida = get_object_or_404(Medida, pk=pk)
        medida.ativo = not medida.ativo
        medida.save(update_fields=["ativo", "atualizado_em"])
        situacao = "ativada" if medida.ativo else "inativada"
        registrar_auditoria(
            request, acao="editar", modulo="medida", registro_id=medida.pk,
            descricao=f"Medida ID {medida.pk} - {medida.medida} foi {situacao}.",
        )
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
            nome, medida_id = medida.medida, medida.pk
            if medida.em_uso():
                medida.ativo = False
                medida.save(update_fields=["ativo", "atualizado_em"])
                registrar_auditoria(
                    request, acao="excluir", modulo="medida", registro_id=medida_id,
                    descricao=f"Medida ID {medida_id} - {nome} foi inativada (em uso; exclusão física bloqueada).",
                )
                messages.warning(request, f'A medida "{nome}" está em uso e não pode ser apagada; ela foi apenas inativada.')
            else:
                medida.delete()
                registrar_auditoria(
                    request, acao="excluir", modulo="medida", registro_id=medida_id,
                    descricao=f"Excluída medida ID {medida_id} - {nome}.",
                )
                messages.success(request, f'Medida "{nome}" excluída com sucesso.')
        return redirect("medida-list")
