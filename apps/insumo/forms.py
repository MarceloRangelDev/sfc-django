from decimal import Decimal

from django import forms

from apps.categoria.models import Categoria

from .models import Insumo


def normalizar_valor_monetario(texto):
    """Aceita '1234.56', '1234,56', '1.234,56' ou 'R$ 1.234,56' e devolve
    uma string decimal padrão ('1234.56') para o DecimalField processar.

    Nunca confia no formato exato vindo do frontend — o usuário pode digitar
    com vírgula (padrão brasileiro) ou colar um valor com o símbolo "R$".
    """
    if texto is None:
        return texto
    texto = str(texto).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return texto
    if "," in texto and "." in texto:
        # "1.234,56" -> ponto é separador de milhar, vírgula é decimal.
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        # "1234,56" -> vírgula é o separador decimal.
        texto = texto.replace(",", ".")
    return texto


class DecimalMonetarioField(forms.DecimalField):
    """DecimalField tolerante ao formato brasileiro de número.

    A checagem de NaN/Infinity/valor não numérico já é feita pelo próprio
    Django (forms.DecimalField.validate() rejeita valores não finitos) —
    aqui só normalizamos o separador decimal antes dessa validação nativa
    rodar.
    """

    def to_python(self, value):
        return super().to_python(normalizar_valor_monetario(value))


class InsumoForm(forms.ModelForm):
    quantidade = DecimalMonetarioField(
        max_digits=15,
        decimal_places=4,
        min_value=Decimal("0"),
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 1,5"}),
        error_messages={
            "invalid": "Informe uma quantidade válida.",
            "min_value": "A quantidade não pode ser negativa.",
        },
    )
    preco = DecimalMonetarioField(
        max_digits=15,
        decimal_places=4,
        min_value=Decimal("0"),
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 12,90"}),
        error_messages={
            "invalid": "Informe um preço válido.",
            "min_value": "O preço não pode ser negativo.",
        },
    )

    class Meta:
        model = Insumo
        fields = ["nome", "categoria", "unidade_medida", "quantidade", "preco", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control"}),
            "categoria": forms.Select(attrs={"class": "form-select"}),
            "unidade_medida": forms.Select(attrs={"class": "form-select"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = cliente
        # Só mostra (e só aceita, ver clean_categoria) categorias do
        # PRÓPRIO cliente — nunca confia em um id de categoria de outro
        # cliente vindo no POST.
        self.fields["categoria"].queryset = Categoria.objects.filter(cliente=cliente).order_by("nome")

    def clean_categoria(self):
        categoria = self.cleaned_data.get("categoria")
        if categoria and categoria.cliente_id != getattr(self.cliente, "pk", None):
            raise forms.ValidationError("Categoria inválida para este cliente.")
        return categoria

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
