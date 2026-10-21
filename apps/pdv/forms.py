from decimal import Decimal

from django import forms

from web_project.forms import MoedaCentavosField

from apps.pdvconfig.models import FormaPagamento, Mesa
from web_project.globais import disponiveis_para

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}


def _moeda(label, obrigatorio=True, **extra):
    return MoedaCentavosField(label=label, required=obrigatorio, widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "0,00"}), **extra)


class AbrirCaixaForm(forms.Form):
    valor_abertura = _moeda("Troco inicial (R$)", obrigatorio=False)

    def clean_valor_abertura(self):
        return self.cleaned_data.get("valor_abertura") or 0


class FecharCaixaForm(forms.Form):
    valor_contado = _moeda("Dinheiro contado no caixa (R$)")
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))


class MovimentoForm(forms.Form):
    valor = _moeda("Valor (R$)")
    motivo = forms.CharField(max_length=255, label="Motivo", widget=forms.TextInput(attrs=CONTROLE))


class ConferenciaForm(forms.Form):
    observacao = forms.CharField(max_length=255, required=False, label="Observação da conferência", widget=forms.TextInput(attrs=CONTROLE))


class CancelarForm(forms.Form):
    motivo = forms.CharField(max_length=255, label="Motivo do cancelamento", widget=forms.TextInput(attrs=CONTROLE))


class DevolucaoForm(forms.Form):
    quantidade = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"), label="Quantidade", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}))
    forma_estorno = forms.ModelChoiceField(queryset=FormaPagamento.objects.none(), label="Devolver por", empty_label="Selecione...", widget=forms.Select(attrs=SELECT))
    repor_estoque = forms.BooleanField(required=False, label="Repor o estoque (só se o item não foi preparado)")
    motivo = forms.CharField(max_length=255, label="Motivo", widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["forma_estorno"].queryset = disponiveis_para(FormaPagamento, cliente, ativo=True).order_by("nome")

    def clean_quantidade(self):
        return self.cleaned_data["quantidade"].quantize(Decimal("0.001"))


class ComandaForm(forms.Form):
    mesa = forms.ModelChoiceField(queryset=Mesa.objects.none(), label="Mesa", empty_label="Selecione...", widget=forms.Select(attrs=SELECT))
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        ocupadas = Mesa.objects.filter(vendas__status="aberta").values("pk")
        self.fields["mesa"].queryset = Mesa.objects.filter(cliente=cliente, ativo=True).exclude(pk__in=ocupadas).order_by("nome")


class ItemComandaForm(forms.Form):
    produto = forms.IntegerField(widget=forms.HiddenInput)
    quantidade = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"), initial=Decimal("1"), label="Quantidade", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}))
    observacao = forms.CharField(max_length=120, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))
