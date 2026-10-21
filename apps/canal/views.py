from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.utils import tirar_foto
from web_project import TemplateLayout
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin

from .forms import CanalForm, TaxaFormSet
from .models import CanalTaxa, CanalVenda
from .services import salvar_canal

class CanalListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Lista paginada de canais (pesquisa, status, ordenação), sempre dentro
    do escopo de unidades do usuário."""

    model = CanalVenda
    template_name = "canal.html"
    context_object_name = "canais"
    incluir_globais = True  # canais globais liberados ao segmento do usuário aparecem (somente leitura)
    colunas_ordenaveis = {"nome": "nome", "taxas": "taxa_percentual_total", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related(Prefetch("taxas", queryset=CanalTaxa.objects.order_by("id")))
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo))
        return self.ordenar(self.filtrar_status(queryset))

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class CanalFormMixin:
    """Canal + formset de taxas, gravados juntos por services.salvar_canal."""

    model = CanalVenda
    form_class = CanalForm
    template_name = "canal_form.html"
    success_url = reverse_lazy("canal-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["solicitante"] = self.request.user  # decide se os campos de canal global aparecem
        return kwargs

    def get_formset(self):
        dados = self.request.POST if self.request.method == "POST" else None
        instancia = self.object if self.object is not None else CanalVenda(cliente=self.request.user.profile.cliente)
        return TaxaFormSet(dados, instance=instancia)

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
        canal = form.instance
        if canal.pk is None:  # só ao criar: o dono é a unidade do usuário
            canal.cliente = self.request.user.profile.cliente
        taxas = [
            CanalTaxa(
                descricao=f.cleaned_data.get("descricao", ""), percentual=f.cleaned_data.get("percentual"),
                valor_fixo=f.cleaned_data.get("valor_fixo"),
            )
            for f in formset.taxas_validas()
        ]
        try:
            salvar_canal(
                self.request, canal, taxas, foto_antes=getattr(self, "_foto_antes", None),
                segmentos=form.cleaned_data.get("segmentos") if "segmentos" in form.fields else None,
            )
        except ValidationError as erro:
            form.add_error(None, erro)
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        messages.success(self.request, self.mensagem_sucesso)
        if "salvar_e_novo" in self.request.POST and self.object is None:
            return redirect("canal-create")
        return redirect(self.success_url)


class CanalCreateView(CanalFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    mensagem_sucesso = "Canal de venda cadastrado com sucesso."


class CanalUpdateView(CanalFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"
    mensagem_sucesso = "Canal de venda atualizado com sucesso."

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)  # antes de o formulário alterar o objeto
        return objeto
