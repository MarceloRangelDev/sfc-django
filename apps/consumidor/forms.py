from django import forms

from web_project.forms import UnidadeFormMixin

from .models import Consumidor

CONTROLE = {"class": "form-control"}


class ConsumidorForm(UnidadeFormMixin, forms.ModelForm):
    """Cadastro de consumidor (cliente final). Normalização e validação ficam em `Consumidor.clean()`
    (vale também para a API); aqui só os widgets, máscaras e o vínculo com a unidade (tenant)."""

    class Meta:
        model = Consumidor
        fields = [
            "nome", "tipo_pessoa", "documento", "email", "telefone", "whatsapp", "instagram", "data_nascimento",
            "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf", "consentimento_lgpd", "ativo",
        ]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE),
            "tipo_pessoa": forms.Select(attrs={"class": "form-select"}),
            "documento": forms.TextInput(attrs={**CONTROLE, "data-mask": "cpf-cnpj", "inputmode": "numeric", "autocomplete": "off"}),
            "email": forms.EmailInput(attrs=CONTROLE),
            "telefone": forms.TextInput(attrs={**CONTROLE, "data-mask": "fone", "inputmode": "tel", "placeholder": "(11) 99999-9999"}),
            "whatsapp": forms.TextInput(attrs={**CONTROLE, "data-mask": "fone", "inputmode": "tel", "placeholder": "(11) 99999-9999"}),
            "instagram": forms.TextInput(attrs={**CONTROLE, "placeholder": "@usuario"}),
            "data_nascimento": forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"),
            "cep": forms.TextInput(attrs={**CONTROLE, "data-mask": "cep", "inputmode": "numeric", "placeholder": "00000-000"}),
            "logradouro": forms.TextInput(attrs=CONTROLE),
            "numero": forms.TextInput(attrs=CONTROLE),
            "complemento": forms.TextInput(attrs=CONTROLE),
            "bairro": forms.TextInput(attrs=CONTROLE),
            "cidade": forms.TextInput(attrs=CONTROLE),
            "uf": forms.TextInput(attrs={**CONTROLE, "maxlength": 2, "style": "text-transform:uppercase"}),
            "consentimento_lgpd": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        # A unidade precisa estar no objeto ANTES de Consumidor.clean() (checagem de documento repetido).
        if not self.instance.pk and "cliente" not in self.fields:
            self.instance.cliente = cliente
