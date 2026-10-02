"""Campos de formulário reutilizáveis (backend). Substituem cópias locais de
"aceitar vírgula brasileira" espalhadas pelos apps."""
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro


class DecimalBRField(forms.DecimalField):
    """DecimalField que aceita o formato brasileiro ('1.234,56', '12,5').

    A checagem de NaN/Infinity, casas decimais e limites continua sendo a do
    Django (max_digits, decimal_places, min_value); aqui só normalizamos o
    texto antes disso.
    """

    def prepare_value(self, value):
        # Valor vindo do banco (Decimal('100.0000')) é mostrado no padrão
        # brasileiro e sem zeros inúteis ('100', '12,5'). Sem isso a máscara
        # decimal do navegador trataria o ponto como lixo e '100.0000'
        # viraria '1000000'.
        if isinstance(value, Decimal):
            return format(value.normalize(), "f").replace(".", ",")
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            value = ler_numero_brasileiro(value)
        except RegraNegocioError:
            raise ValidationError(self.error_messages["invalid"], code="invalid") from None
        return super().to_python(str(value))


class MoedaCentavosField(forms.Field):
    """Campo de dinheiro: o usuário digita 'R$ 1.234,56' e o cleaned_data é
    um INTEIRO em centavos (123456) — formato de persistência do projeto
    (ver web_project/regras/dinheiro.py). Nunca passa por float."""

    default_error_messages = {
        "invalid": "Informe um valor válido.",
        "min_value": "O valor não pode ser negativo.",
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={"class": "form-control", "data-mask": "moeda"}))
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        # Valor inicial vem em centavos (do banco); mostra '12,90'.
        if isinstance(value, int) and not isinstance(value, bool):
            return DinheiroService.formatar(value).replace("R$ ", "")
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            centavos = DinheiroService.de_texto(value)
        except RegraNegocioError:
            raise ValidationError(self.error_messages["invalid"], code="invalid") from None
        if centavos < 0:
            raise ValidationError(self.error_messages["min_value"], code="min_value")
        return centavos
