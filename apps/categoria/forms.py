from django import forms

from web_project.forms import MoedaCentavosField, UnidadeFormMixin

from .models import Categoria

LIMITE_FATURAMENTO = 10**15  # centavos; mesmo teto de segurança dos insumos


class CategoriaForm(UnidadeFormMixin, forms.ModelForm):
    quantidade_vendida = forms.IntegerField(
        min_value=0, max_value=10**9, label="Quantidade vendida", initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1, "data-prev": "quantidade"}),
        error_messages={"invalid": "Informe um número inteiro.", "min_value": "A quantidade não pode ser negativa."},
    )
    faturamento = MoedaCentavosField(
        label="Faturamento (R$)", initial=0,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 1.500,00", "data-mask": "moeda", "data-prev": "faturamento"}),
    )

    class Meta:
        model = Categoria
        fields = ["nome", "quantidade_vendida", "faturamento", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)

    def clean_faturamento(self):
        valor = self.cleaned_data.get("faturamento")
        if valor is not None and valor > LIMITE_FATURAMENTO:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor or 0

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome da categoria é obrigatório.")
        queryset = Categoria.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Esta categoria já está cadastrada.")
        return nome
