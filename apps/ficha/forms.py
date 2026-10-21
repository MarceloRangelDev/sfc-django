from decimal import Decimal

from django import forms
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.maodeobra.models import Colaborador
from apps.processado.forms import BaseItemFormSet, CamposReceitaMixin, ProcessadoItemForm
from apps.produto.models import Produto
from web_project.forms import DecimalBRField

from .models import Ficha, FichaItem, FichaMaoDeObra
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


class FichaMaoDeObraForm(forms.ModelForm):
    horas = DecimalBRField(
        max_digits=7, decimal_places=2, min_value=Decimal("0.01"), label="Horas",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe as horas.", "min_value": "As horas devem ser maiores que zero."},
    )

    class Meta:
        model = FichaMaoDeObra
        fields = ["colaborador", "horas"]
        widgets = {"colaborador": forms.Select(attrs={"class": "form-select"})}

    def __init__(self, *args, colaboradores=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Só colaboradores da MESMA unidade (tenant); o campo rejeita ids de fora.
        self.fields["colaborador"].queryset = colaboradores if colaboradores is not None else Colaborador.objects.none()
        self.fields["colaborador"].empty_label = "Selecione..."


class BaseMaoDeObraFormSet(BaseInlineFormSet):
    def __init__(self, *args, cliente=None, **kwargs):
        # Ativos da unidade; os já usados nesta ficha continuam válidos mesmo se forem desativados depois.
        filtro = Q(cliente=cliente, ativo=True)
        instancia = kwargs.get("instance")
        if instancia is not None and instancia.pk:
            filtro |= Q(pk__in=instancia.mao_de_obra.values("colaborador_id"), cliente=cliente)
        self.colaboradores = Colaborador.objects.filter(filtro).order_by("nome")
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["colaboradores"] = self.colaboradores
        return kwargs

    def linhas_validas(self):
        return [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ids = [f.cleaned_data["colaborador"].pk for f in self.linhas_validas()]
        if len(ids) != len(set(ids)):
            raise forms.ValidationError("Um colaborador só pode aparecer uma vez na ficha; some as horas.")


MaoDeObraFormSet = inlineformset_factory(
    Ficha, FichaMaoDeObra, form=FichaMaoDeObraForm, formset=BaseMaoDeObraFormSet, extra=1, can_delete=True,
)
