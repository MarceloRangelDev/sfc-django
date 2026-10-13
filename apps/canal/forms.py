from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from web_project.forms import DecimalBRField, MoedaCentavosField

from .models import CanalTaxa, CanalVenda

CONTROLE = {"class": "form-control"}
LIMITE_FIXO = 10**12  # centavos (R$ 10 bilhões por venda: teto de segurança)


class CanalForm(forms.ModelForm):
    class Meta:
        model = CanalVenda
        fields = ["nome", "descricao", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE),
            "descricao": forms.TextInput(attrs=CONTROLE),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente

    def clean(self):
        dados = super().clean()
        nome = (dados.get("nome") or "").strip()
        descricao = (dados.get("descricao") or "").strip()
        if nome:
            repetido = CanalVenda.objects.filter(
                cliente=self.cliente, nome__iexact=nome, descricao__iexact=descricao
            ).exclude(pk=self.instance.pk)
            if repetido.exists():
                raise forms.ValidationError("Já existe um canal com este nome e esta descrição.")
        dados["nome"], dados["descricao"] = nome, descricao
        return dados


class CanalTaxaForm(forms.ModelForm):
    """Uma taxa: informe o percentual OU o valor fixo (nunca os dois)."""

    percentual = DecimalBRField(
        required=False, max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("99.9999"),
        label="Percentual (%)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 26,20", "data-mask": "decimal"}),
        error_messages={"invalid": "Informe um percentual válido.", "max_value": "O percentual deve ser menor que 100."},
    )
    valor_fixo = MoedaCentavosField(
        required=False, label="Valor fixo (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 1,59", "data-mask": "moeda"}),
    )

    class Meta:
        model = CanalTaxa
        fields = ["descricao", "percentual", "valor_fixo"]
        widgets = {"descricao": forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: Comissão"})}

    def clean_valor_fixo(self):
        valor = self.cleaned_data.get("valor_fixo")
        if valor is not None and valor > LIMITE_FIXO:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor

    def clean(self):
        dados = super().clean()
        if self.errors or not self.has_changed():
            return dados
        if (dados.get("percentual") is None) == (dados.get("valor_fixo") is None):
            raise forms.ValidationError("Informe o percentual ou o valor fixo da taxa (apenas um dos dois).")
        return dados


class BaseTaxaFormSet(BaseInlineFormSet):
    def taxas_validas(self):
        return [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]


TaxaFormSet = inlineformset_factory(
    CanalVenda, CanalTaxa, form=CanalTaxaForm, formset=BaseTaxaFormSet, extra=1, can_delete=True,
)
