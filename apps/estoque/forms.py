from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from apps.insumo.models import Insumo
from apps.medida.models import Medida
from web_project.forms import DecimalBRField

from .models import MovimentoEstoque

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}

# Tipos que o usuário lança manualmente (as transferências têm tela própria).
TIPOS_MANUAIS = [
    (MovimentoEstoque.ENTRADA, "Entrada (compra/recebimento)"),
    (MovimentoEstoque.SAIDA, "Saída (consumo/perda)"),
    (MovimentoEstoque.AJUSTE_ENTRADA, "Ajuste de inventário (acréscimo)"),
    (MovimentoEstoque.AJUSTE_SAIDA, "Ajuste de inventário (redução)"),
]
LIMITE_QUANTIDADE = Decimal("100000000000")  # 10^11: cabe em Decimal(15,4)


def _quantidade(label="Quantidade"):
    return DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), max_value=LIMITE_QUANTIDADE, label=label,
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "A quantidade deve ser maior que zero.",
                        "max_value": "Quantidade acima do limite permitido."},
    )


class MovimentoForm(forms.Form):
    """Entrada, saída ou ajuste de UM insumo da unidade do usuário."""

    insumo = forms.ModelChoiceField(queryset=Insumo.objects.none(), label="Insumo", widget=forms.Select(attrs=SELECT))
    tipo = forms.ChoiceField(choices=TIPOS_MANUAIS, label="Tipo", widget=forms.Select(attrs=SELECT))
    quantidade = _quantidade()
    medida = forms.ModelChoiceField(queryset=Medida.objects.none(), label="Unidade", widget=forms.Select(attrs=SELECT))
    lote = forms.CharField(max_length=60, required=False, label="Lote", widget=forms.TextInput(attrs=CONTROLE))
    validade = forms.DateField(required=False, label="Validade", widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}))
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Só insumos ATIVOS da unidade do usuário (o campo rejeita ids de fora).
        self.fields["insumo"].queryset = Insumo.objects.filter(cliente=cliente, ativo=True).select_related("medida").order_by("nome")
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nome} ({i.medida.sigla})"
        self.fields["insumo"].empty_label = "Selecione..."
        self.fields["medida"].queryset = Medida.objects.filter(ativo=True).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."

    def clean(self):
        dados = super().clean()
        tipo, lote, validade = dados.get("tipo"), (dados.get("lote") or "").strip(), dados.get("validade")
        entrada = tipo in (MovimentoEstoque.ENTRADA, MovimentoEstoque.AJUSTE_ENTRADA)
        if not entrada and (lote or validade):
            # Saídas seguem o PEPS (lote que vence antes): lote/validade digitados seriam ignorados.
            raise forms.ValidationError("Saídas e reduções consomem automaticamente o lote que vence primeiro; deixe lote e validade em branco.")
        # Validade sem lote é válida: o serviço usa um lote automático pela validade.
        dados["lote"] = lote
        return dados


class MovimentoEdicaoForm(forms.Form):
    """Correção de uma movimentação existente. Insumo, tipo e lote ficam fixos (só leitura na
    tela); a validade só aparece para entradas (o serviço revalida tudo)."""

    quantidade = _quantidade()
    medida = forms.ModelChoiceField(queryset=Medida.objects.none(), label="Unidade", widget=forms.Select(attrs=SELECT))
    data = forms.DateTimeField(
        label="Data", input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={**CONTROLE, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    validade = forms.DateField(required=False, label="Validade", widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"))
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, entrada=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medida"].queryset = Medida.objects.filter(ativo=True).order_by("id")
        if not entrada:  # saída/redução consome lotes existentes: não há validade a editar
            del self.fields["validade"]


class MinimoForm(forms.Form):
    minimo = DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0"), max_value=LIMITE_QUANTIDADE, label="Quantidade mínima",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "O mínimo não pode ser negativo."},
    )


class TransferenciaForm(forms.Form):
    destino = forms.ModelChoiceField(queryset=None, label="Unidade de destino", widget=forms.Select(attrs=SELECT))
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, destinos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destino"].queryset = destinos  # só unidades da mesma estrutura (view decide)
        self.fields["destino"].empty_label = "Selecione..."


class ItemTransferenciaForm(forms.Form):
    insumo = forms.ModelChoiceField(queryset=Insumo.objects.none(), label="Insumo", widget=forms.Select(attrs=SELECT))
    quantidade = _quantidade()
    medida = forms.ModelChoiceField(queryset=Medida.objects.none(), label="Unidade", widget=forms.Select(attrs=SELECT))

    def __init__(self, *args, insumos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["insumo"].queryset = insumos if insumos is not None else Insumo.objects.none()
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nome} ({i.medida.sigla})"
        self.fields["insumo"].empty_label = "Selecione..."
        self.fields["medida"].queryset = Medida.objects.filter(ativo=True).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."


class BaseItensFormSet(BaseFormSet):
    def __init__(self, *args, insumos=None, **kwargs):
        self.insumos = insumos
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "insumos": self.insumos}

    def itens_validos(self):
        return [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]

    def clean(self):
        if any(self.errors):
            return
        itens = self.itens_validos()
        if not itens:
            raise forms.ValidationError("Inclua ao menos um item na transferência.")
        ids = [f.cleaned_data["insumo"].pk for f in itens]
        if len(ids) != len(set(ids)):
            raise forms.ValidationError("Um insumo só pode aparecer uma vez na transferência; some as quantidades.")


ItensTransferenciaFormSet = formset_factory(ItemTransferenciaForm, formset=BaseItensFormSet, extra=1, can_delete=True)
