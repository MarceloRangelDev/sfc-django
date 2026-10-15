import re

from django import forms

from apps.cliente.models import Cliente

from .models import ConfiguracaoFiscal

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
CHECK = {"class": "form-check-input"}


class ConfiguracaoFiscalForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoFiscal
        fields = ["ambiente", "provedor", "habilita_nfce", "habilita_nfe", "habilita_nfse", "inscricao_municipal", "inscricao_estadual", "codigo_municipio", "serie"]
        widgets = {
            "ambiente": forms.Select(attrs=SELECT), "provedor": forms.Select(attrs=SELECT),
            "habilita_nfce": forms.CheckboxInput(attrs=CHECK), "habilita_nfe": forms.CheckboxInput(attrs=CHECK), "habilita_nfse": forms.CheckboxInput(attrs=CHECK),
            "inscricao_municipal": forms.TextInput(attrs={**CONTROLE, "maxlength": 20}), "inscricao_estadual": forms.TextInput(attrs={**CONTROLE, "maxlength": 20}),
            "codigo_municipio": forms.TextInput(attrs={**CONTROLE, "maxlength": 7, "inputmode": "numeric"}), "serie": forms.NumberInput(attrs={**CONTROLE, "min": 1}),
        }

    def clean_codigo_municipio(self):
        codigo = (self.cleaned_data.get("codigo_municipio") or "").strip()
        if codigo and not re.fullmatch(r"\d{7}", codigo):
            raise forms.ValidationError("O código IBGE do município tem 7 dígitos.")
        return codigo


class ClienteFiscalForm(forms.ModelForm):
    """Só os dois campos fiscais do cadastro da empresa (o resto do Cliente é editado na tela de Clientes)."""

    class Meta:
        model = Cliente
        fields = ["regime_tributario", "emite_nota_fiscal"]
        widgets = {"regime_tributario": forms.Select(attrs=SELECT), "emite_nota_fiscal": forms.CheckboxInput(attrs=CHECK)}


class CertificadoForm(forms.Form):
    arquivo = forms.FileField(label="Certificado A1 (.pfx / .p12)", widget=forms.ClearableFileInput(attrs={**CONTROLE, "accept": ".pfx,.p12"}))
    senha = forms.CharField(label="Senha do certificado", widget=forms.PasswordInput(attrs={**CONTROLE, "autocomplete": "new-password"}, render_value=False), max_length=200)


class SolicitarForm(forms.Form):
    tipo = forms.ChoiceField(choices=[("nfce", "NFC-e"), ("nfe", "NF-e"), ("nfse", "NFS-e")])
