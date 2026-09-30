from django import forms

from .models import Cliente


class ClienteForm(forms.ModelForm):
    """A validação/normalização real do CPF/CNPJ acontece em
    Cliente.clean() (chamado por ModelForm.is_valid() -> instance.full_clean()),
    não aqui — assim ela vale também para quem gravar um Cliente fora deste
    formulário (Django Admin, um script, etc.)."""

    class Meta:
        model = Cliente
        fields = ["tipo_pessoa", "cpf_cnpj", "nome_razao_social", "nome_fantasia", "ativo"]
        widgets = {
            "tipo_pessoa": forms.Select(attrs={"class": "form-select"}),
            "cpf_cnpj": forms.TextInput(attrs={"class": "form-control", "placeholder": "Somente números ou com máscara"}),
            "nome_razao_social": forms.TextInput(attrs={"class": "form-control"}),
            "nome_fantasia": forms.TextInput(attrs={"class": "form-control"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
