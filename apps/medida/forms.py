from decimal import Decimal

from django import forms

from web_project.forms import DecimalBRField

from .models import Medida


class MedidaForm(forms.ModelForm):
    fator_base = DecimalBRField(
        max_digits=20,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        label="Fator de conversão",
        help_text="Quanto vale 1 desta unidade na unidade base da dimensão (ex.: 1 quilo = 1000 gramas).",
        widget=forms.TextInput(attrs={"class": "form-control", "data-mask": "decimal"}),
        error_messages={
            "invalid": "Informe um fator válido.",
            "min_value": "O fator de conversão deve ser maior que zero.",
        },
    )

    class Meta:
        model = Medida
        fields = [
            "medida", "sigla", "descricao", "equivale", "dimensao", "fator_base",
            "nome_singular", "nome_plural", "ativo",
        ]
        widgets = {
            "medida": forms.TextInput(attrs={"class": "form-control"}),
            "sigla": forms.TextInput(attrs={"class": "form-control"}),
            "descricao": forms.TextInput(attrs={"class": "form-control"}),
            "equivale": forms.TextInput(attrs={"class": "form-control"}),
            "dimensao": forms.Select(attrs={"class": "form-select"}),
            "nome_singular": forms.TextInput(attrs={"class": "form-control"}),
            "nome_plural": forms.TextInput(attrs={"class": "form-control"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_medida(self):
        return self._texto_unico("medida", "Já existe uma medida com este nome.")

    def clean_sigla(self):
        return self._texto_unico("sigla", "Já existe uma medida com esta sigla.")

    def _texto_unico(self, campo, mensagem):
        valor = (self.cleaned_data.get(campo) or "").strip()
        if not valor:
            raise forms.ValidationError("Campo obrigatório.")
        # Comparação sem diferenciar maiúsculas ("Litro" x "litro"); a
        # sigla "L" (litro) e "l" não podem coexistir.
        duplicadas = Medida.objects.filter(**{f"{campo}__iexact": valor})
        if self.instance.pk:
            duplicadas = duplicadas.exclude(pk=self.instance.pk)
        if duplicadas.exists():
            raise forms.ValidationError(mensagem)
        return valor

    def clean(self):
        dados = super().clean()
        # Se a medida já foi usada (ex.: por insumos com preço calculado),
        # mudar a dimensão ou o fator alteraria o significado dos valores
        # já gravados. Bloqueamos; para mudar de verdade, cadastre uma
        # nova medida e inative esta (documentado no README).
        if self.instance.pk and self.instance.em_uso():
            for campo in ("dimensao", "fator_base"):
                if campo in dados and campo in self.changed_data:
                    self.add_error(
                        campo,
                        "Esta medida já está em uso; não é possível alterar a dimensão nem o fator de conversão. "
                        "Cadastre uma nova medida e inative esta.",
                    )
        return dados
