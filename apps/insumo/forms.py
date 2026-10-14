from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.medida.models import Medida
from apps.estoque.services import insumo_tem_estoque
from apps.processado.services import mensagem_insumo_em_uso
from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin
from web_project.regras import RegraNegocioError

from .models import LIMITE_CENTAVOS, Insumo


class InsumoForm(UnidadeFormMixin, forms.ModelForm):
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
        fields = ["nome", "marca", "medida", "quantidade", "preco_pago", "dias_validade", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control"}),
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Opcional", "maxlength": 60}),
            "medida": forms.Select(attrs={"class": "form-select", "data-calc": "medida"}),
            "dias_validade": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 32000, "placeholder": "Opcional (ex.: 5)"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
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
        queryset = Insumo.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Este insumo já está cadastrado.")
        return nome

    def clean_marca(self):
        # Opcional: espaços nas pontas e repetidos são normalizados; vazio continua válido.
        return " ".join((self.cleaned_data.get("marca") or "").split())

    def clean_preco_pago(self):
        valor = self.cleaned_data.get("preco_pago")
        if valor is not None and valor > LIMITE_CENTAVOS:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor

    def clean(self):
        dados = super().clean()
        medida_nova = dados.get("medida")
        if self.instance.pk and medida_nova and medida_nova.pk != self.instance.medida_id and insumo_tem_estoque(self.instance):
            # O saldo do estoque está guardado na unidade atual: trocá-la mudaria o significado dele.
            self.add_error("medida", "Este insumo tem movimentações de estoque; a unidade de medida não pode ser alterada.")
        if self.instance.pk and medida_nova and medida_nova.dimensao != self.instance.medida.dimensao:
            # As receitas que usam este insumo guardam unidades da dimensão antiga;
            # trocar (ex.: kg -> L) tornaria o custo delas incalculável.
            erro = mensagem_insumo_em_uso(self.instance)
            if erro:
                self.add_error("medida", "Este insumo é usado em receitas; a unidade deve continuar na mesma dimensão.")
        # Antecipa o erro de cálculo (ex.: preço por unidade gigante) com
        # mensagem amigável em vez de falhar só ao gravar.
        if all(dados.get(campo) is not None for campo in ("medida", "quantidade", "preco_pago")):
            tentativa = Insumo(medida=dados["medida"], quantidade=dados["quantidade"], preco_pago=dados["preco_pago"])
            try:
                tentativa.calcular_preco_unidade()
            except RegraNegocioError as erro:
                raise forms.ValidationError(str(erro)) from None
        return dados
