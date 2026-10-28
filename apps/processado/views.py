from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.utils import tirar_foto
from web_project import TemplateLayout
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin
from web_project.acesso import clientes_dos_itens
from web_project.dimensoes import dimensoes_de

from apps.ficha.views import FichaCalcularView

from .forms import ItemFormSet, ProcessadoForm
from .models import Processado, ProcessadoItem
from .services import excluir_processado, salvar_processado

class ProcessadoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Lista paginada com pesquisa, status e ordenação, tudo no banco e
    sempre dentro do escopo de unidades do usuário (matriz/filiais)."""

    model = Processado
    template_name = "processado.html"
    context_object_name = "processados"
    colunas_ordenaveis = {"nome": "nome", "custo": "custo_total", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().select_related("rendimento_medida")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo))
        return self.ordenar(self.filtrar_status(queryset))

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ProcessadoFormMixin:
    """Formulário do processado + formset da ficha técnica, gravados juntos
    por services.salvar_processado (uma transação)."""

    model = Processado
    form_class = ProcessadoForm
    template_name = "processado_form.html"
    success_url = reverse_lazy("processado")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["ver_receita"] = self.request.user.has_perm("processado.ver_receita")  # receita só com a permissão
        return kwargs

    def get_formset(self):
        dados = self.request.POST if self.request.method == "POST" else None
        instancia = self.object if self.object is not None else Processado(cliente=self.request.user.profile.cliente)
        return ItemFormSet(dados, instance=instancia, cliente=instancia.cliente)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.setdefault("formset", self.get_formset())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if isinstance(self, UpdateView) else None
        form = self.get_form()
        formset = self.get_formset()
        if form.is_valid() and formset.is_valid():
            return self.form_valid(form, formset)
        return self.render_to_response(self.get_context_data(form=form, formset=formset))

    def form_valid(self, form, formset):
        processado = form.instance
        if processado.pk is None:  # só ao criar: o dono é a unidade do usuário
            processado.cliente = self.request.user.profile.cliente
        itens = [
            ProcessadoItem(
                insumo=f.cleaned_data["insumo"], medida=f.cleaned_data["medida"], quantidade=f.cleaned_data["quantidade"], **dimensoes_de(f)
            )
            for f in formset.itens_validos()
        ]
        try:
            salvar_processado(self.request, processado, itens, foto_antes=getattr(self, "_foto_antes", None))
        except ValidationError as erro:
            form.add_error(None, erro)
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        messages.success(self.request, self.mensagem_sucesso)
        if "salvar_e_novo" in self.request.POST and self.object is None:  # "Salvar e adicionar outro"
            return redirect("processado-create")
        return redirect(self.success_url)


class ProcessadoCalcularView(FichaCalcularView):
    """Prévia (sem gravar) do custo de cada linha da ficha técnica do processado e do total, enquanto o
    formulário é preenchido. É a mesma conta da gravação (`custo_do_item`), a mesma da ficha técnica;
    só os itens do escopo de visualização do usuário entram (tenant). Ver apps/ficha/views.py."""


class ProcessadoCreateView(ProcessadoFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    mensagem_sucesso = "Processado cadastrado com sucesso. Ele já está disponível na lista de insumos."


class ProcessadoUpdateView(ProcessadoFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"
    mensagem_sucesso = "Processado atualizado. Os custos dependentes foram recalculados."

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)  # antes de o formulário alterar o objeto
        return objeto


class ProcessadoExcluirView(View):
    """Exclusão lógica; bloqueada se o insumo gerado é usado por outra receita."""

    def post(self, request, pk):
        # Escopo de exclusão: unidade própria e, para a matriz com permissão, as filiais.
        processado = get_object_or_404(
            Processado.objects.select_related("insumo"), pk=pk, cliente__in=clientes_dos_itens(request.user, "delete")
        )
        try:
            excluir_processado(request, processado)
        except ValidationError as erro:
            messages.error(request, " ".join(erro.messages))
        else:
            messages.success(request, f'Processado "{processado.nome}" excluído com sucesso.')
        return redirect("processado")
