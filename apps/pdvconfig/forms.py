from decimal import Decimal

from django import forms

from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin
from web_project.globais import GlobalFormMixin

from .models import ConfiguracaoPdv, FormaPagamento, Mesa

CONTROLE = {"class": "form-control"}


class FormaPagamentoForm(GlobalFormMixin, UnidadeFormMixin, forms.ModelForm):
    """Forma de pagamento + como a taxa é cobrada (sem taxa, percentual, valor fixo por transação ou ambos)."""

    taxa_percentual = DecimalBRField(
        required=False, max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("99.9999"), label="Taxa percentual (%)",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal", "placeholder": "Ex.: 3,5"}),
        error_messages={"invalid": "Informe um percentual válido.", "max_value": "O percentual deve ser menor que 100."},
    )
    taxa_fixa = MoedaCentavosField(
        required=False, label="Taxa fixa por transação (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "Ex.: 0,50"}),
    )

    class Meta:
        model = FormaPagamento
        fields = ["nome", "tipo", "taxa_modo", "taxa_percentual", "taxa_fixa", "ativo", "eh_global", "segmentos"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE),
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "taxa_modo": forms.RadioSelect,
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "eh_global": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "segmentos": forms.SelectMultiple(),
        }

    def __init__(self, *args, cliente=None, unidades=None, solicitante=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        self.configurar_global(solicitante, self.instance.cliente if self.instance.pk else cliente)  # só o administrador do sistema vê os campos globais
        self.fields["taxa_modo"].choices = FormaPagamento.MODO_CHOICES  # sem a opção vazia do RadioSelect

    def clean_taxa_percentual(self):
        return self.cleaned_data.get("taxa_percentual") or Decimal("0")

    def clean_taxa_fixa(self):
        return self.cleaned_data.get("taxa_fixa") or 0

    def clean(self):
        return self.validar_global(super().clean())

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome da forma de pagamento.")
        repetida = FormaPagamento.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError("Já existe uma forma de pagamento com este nome.")
        return nome


class MesaForm(UnidadeFormMixin, forms.ModelForm):
    class Meta:
        model = Mesa
        fields = ["nome", "ativo"]
        widgets = {"nome": forms.TextInput(attrs=CONTROLE), "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"})}

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome da mesa.")
        repetida = Mesa.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError("Já existe uma mesa com este nome.")
        return nome


class ConfiguracaoPdvForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoPdv
        fields = ["controla_estoque", "confirmacao_pedido", "formato_comprovante", "rodape_comprovante"]
        widgets = {
            "controla_estoque": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "confirmacao_pedido": forms.Select(attrs={"class": "form-select"}),
            "formato_comprovante": forms.Select(attrs={"class": "form-select"}),
            "rodape_comprovante": forms.TextInput(attrs={**CONTROLE, "maxlength": 120}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["confirmacao_pedido"].required = False  # vazio = mantém a regra atual (clientes antigos da API/tela)

    def clean_confirmacao_pedido(self):
        return self.cleaned_data.get("confirmacao_pedido") or self.instance.confirmacao_pedido
