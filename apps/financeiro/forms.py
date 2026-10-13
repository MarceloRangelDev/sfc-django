from datetime import timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from web_project.forms import MoedaCentavosField

from .models import TIPO_CATEGORIA_CHOICES, CategoriaFinanceira

CONTROLE = {"class": "form-control"}
LIMITE_VALOR = 10**12  # centavos: teto de segurança
DATA = forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d")


def _valor(label):
    return MoedaCentavosField(
        label=label, widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 1.250,00", "data-mask": "moeda"}),
    )


class ContaForm(forms.ModelForm):
    """Conta a pagar/receber. Subclasses (via `conta_form_para`) definem o model e
    o tipo de categoria aceito (despesa para contas a pagar, receita para a receber)."""

    tipo_categoria = None
    valor = _valor("Valor (R$)")
    emissao = forms.DateField(label="Emissão", widget=DATA, initial=timezone.localdate)
    vencimento = forms.DateField(label="Vencimento", widget=DATA)

    class Meta:
        fields = ["descricao", "contraparte", "categoria", "valor", "emissao", "vencimento", "observacao"]
        widgets = {
            "descricao": forms.TextInput(attrs=CONTROLE),
            "contraparte": forms.TextInput(attrs=CONTROLE),
            "categoria": forms.Select(attrs={"class": "form-select"}),
            "observacao": forms.TextInput(attrs=CONTROLE),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Só categorias ATIVAS da unidade e do tipo certo (o campo rejeita ids de fora);
        # ao editar, a categoria atual segue válida mesmo se desativada depois.
        filtro = Q(cliente=self.cliente, tipo=self.tipo_categoria, ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.categoria_id)
        self.fields["categoria"].queryset = CategoriaFinanceira.objects.filter(filtro).order_by("nome")
        self.fields["categoria"].empty_label = "Selecione..."

    def clean_valor(self):
        valor = self.cleaned_data.get("valor")
        if not valor or valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        if valor > LIMITE_VALOR:
            raise forms.ValidationError("Valor acima do limite permitido.")
        if self.instance.pk and valor < self.instance.valor_baixado:
            raise forms.ValidationError("O valor não pode ser menor que o já baixado; estorne as baixas antes.")
        return valor

    def clean(self):
        dados = super().clean()
        emissao, vencimento = dados.get("emissao"), dados.get("vencimento")
        if emissao and vencimento and vencimento < emissao:
            self.add_error("vencimento", "O vencimento não pode ser anterior à emissão.")
        return dados


def conta_form_para(modelo, tipo_categoria):
    """ContaForm ligada ao model da conta (ContaPagar ou ContaReceber)."""
    meta = type("Meta", (ContaForm.Meta,), {"model": modelo})
    return type(f"{modelo.__name__}Form", (ContaForm,), {"Meta": meta, "tipo_categoria": tipo_categoria})


class BaixaForm(forms.Form):
    valor = _valor("Valor (R$)")
    data = forms.DateField(label="Data", widget=DATA, initial=timezone.localdate)
    observacao = forms.CharField(max_length=255, required=False, label="Observação", widget=forms.TextInput(attrs=CONTROLE))

    def clean_valor(self):
        valor = self.cleaned_data.get("valor")
        if not valor or valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor

    def clean_data(self):
        data = self.cleaned_data.get("data")
        if data and data > timezone.localdate():
            raise forms.ValidationError("A data da baixa não pode ser futura.")
        return data


class CategoriaFinanceiraForm(forms.ModelForm):
    class Meta:
        model = CategoriaFinanceira
        fields = ["nome", "tipo"]
        widgets = {"nome": forms.TextInput(attrs=CONTROLE), "tipo": forms.Select(attrs={"class": "form-select"})}

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = cliente
        self.fields["tipo"].choices = TIPO_CATEGORIA_CHOICES

    def clean_nome(self):
        return (self.cleaned_data.get("nome") or "").strip()

    def clean(self):
        dados = super().clean()
        nome, tipo = dados.get("nome"), dados.get("tipo")
        if nome and tipo and CategoriaFinanceira.objects.filter(cliente=self.cliente, tipo=tipo, nome__iexact=nome).exists():
            self.add_error("nome", "Já existe uma categoria com este nome e tipo.")
        return dados


class PeriodoForm(forms.Form):
    """Período e regime do DRE. Padrão: mês corrente, caixa."""

    inicio = forms.DateField(required=False, widget=DATA, label="De")
    fim = forms.DateField(required=False, widget=DATA, label="Até")
    regime = forms.ChoiceField(choices=[("caixa", "Caixa (pagamentos e recebimentos)"), ("vencimento", "Vencimento (contas)")], required=False)

    def periodo(self):
        hoje = timezone.localdate()
        dados = self.cleaned_data if self.is_valid() else {}
        inicio = dados.get("inicio") or hoje.replace(day=1)
        fim = dados.get("fim") or (hoje.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return inicio, fim, dados.get("regime") or "caixa"
