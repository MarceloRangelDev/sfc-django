from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.medida.models import Medida
from web_project.forms import DecimalBRField, MoedaCentavosField
from web_project.regras import RegraNegocioError

from .models import LIMITE_CENTAVOS, Insumo


class InsumoForm(forms.ModelForm):
    """Cadastro/edição de insumo.

    O usuário informa: nome, unidade comprada, quantidade comprada e valor
    pago. O preço por KG/L/UN NÃO é um campo do formulário: é calculado no
    backend (Insumo.save) a partir desses três dados — a tela só mostra uma
    prévia (via /medidas/calcular), nunca decide o valor.
    """

    quantidade = DecimalBRField(
        max_digits=15,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        label="Quantidade comprada",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 100", "data-mask": "decimal", "data-calc": "quantidade"}),
        error_messages={
            "invalid": "Informe uma quantidade válida.",
            "min_value": "A quantidade deve ser maior que zero.",
        },
    )
    preco_pago = MoedaCentavosField(
        label="Valor pago (R$)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 4,00", "data-mask": "moeda", "data-calc": "preco"}),
        error_messages={"invalid": "Informe um valor válido.", "min_value": "O valor não pode ser negativo."},
    )

    class Meta:
        model = Insumo
        fields = ["nome", "medida", "quantidade", "preco_pago", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control"}),
            "medida": forms.Select(attrs={"class": "form-select", "data-calc": "medida"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = cliente
        # Só medidas ativas aparecem para novos cadastros; ao editar, a
        # medida atual do insumo continua selecionável mesmo se inativada.
        filtro = Q(ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.medida_id)
        self.fields["medida"].queryset = Medida.objects.filter(filtro).order_by("id")
        self.fields["medida"].empty_label = "Selecione..."

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome do insumo é obrigatório.")
        queryset = Insumo.objects.filter(cliente=self.cliente, nome__iexact=nome)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Este insumo já está cadastrado.")
        return nome

    def clean_preco_pago(self):
        valor = self.cleaned_data.get("preco_pago")
        if valor is not None and valor > LIMITE_CENTAVOS:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor

    def clean(self):
        dados = super().clean()
        # Antecipa o erro de cálculo (ex.: preço por unidade gigante) com
        # mensagem amigável em vez de falhar só ao gravar.
        if all(dados.get(campo) is not None for campo in ("medida", "quantidade", "preco_pago")):
            tentativa = Insumo(medida=dados["medida"], quantidade=dados["quantidade"], preco_pago=dados["preco_pago"])
            try:
                tentativa.calcular_preco_unidade()
            except RegraNegocioError as erro:
                raise forms.ValidationError(str(erro)) from None
        return dados
