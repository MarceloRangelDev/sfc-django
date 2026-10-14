from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from apps.processado.forms import BaseItemFormSet, CamposReceitaMixin, ProcessadoItemForm
from apps.produto.models import Produto

from .models import Ficha, FichaItem
from .services import nome_da_ficha

CONTROLE = {"class": "form-control"}


class FichaForm(CamposReceitaMixin, forms.ModelForm):
    class Meta:
        model = Ficha
        fields = ["produto", "receita", "observacoes", "ativo"]
        widgets = {
            "produto": forms.Select(attrs={"class": "form-select"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, ver_receita=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_receita(ver_receita)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Só produtos ATIVOS da mesma unidade; ao editar, o produto atual segue válido.
        filtro = Q(cliente=self.cliente, ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.produto_id)
        self.fields["produto"].queryset = Produto.objects.filter(filtro).order_by("nome")
        self.fields["produto"].empty_label = "Selecione..."

    def clean(self):
        dados = super().clean()
        produto = dados.get("produto")
        if produto is not None:
            # A ficha é do produto: o nome interno (coluna `nome`, única por unidade) é derivado dele.
            self.instance.nome = nome_da_ficha(self.cliente, produto, ignorar_pk=self.instance.pk)
        return dados


class FichaItemForm(ProcessadoItemForm):
    """Mesma linha de insumo/unidade/quantidade dos processados (o seletor de
    insumos é limitado à unidade da ficha pelo formset)."""

    class Meta(ProcessadoItemForm.Meta):
        model = FichaItem


# BaseItemFormSet (apps/processado/forms.py): insumos da unidade, sem repetição, ao menos um item.
ItemFormSet = inlineformset_factory(
    Ficha, FichaItem, form=FichaItemForm, formset=BaseItemFormSet, extra=1, can_delete=True,
)
