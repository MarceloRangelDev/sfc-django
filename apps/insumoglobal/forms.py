from django import forms

from apps.medida.models import Medida
from apps.modulo.models import Segmento

from .models import InsumoGlobal, normalizar


class InsumoGlobalForm(forms.ModelForm):
    class Meta:
        model = InsumoGlobal
        fields = ["nome", "medida", "descricao", "segmentos", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control"}),
            "medida": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs={"class": "form-control", "maxlength": 255}),
            "segmentos": forms.CheckboxSelectMultiple(),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medida"].queryset = Medida.objects.filter(ativo=True).order_by("id")
        self.fields["medida"].empty_label = "Sem sugestão"
        self.fields["segmentos"].queryset = Segmento.objects.filter(ativo=True).order_by("nome")
        self.fields["segmentos"].required = False

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome padrão.")
        repetido = InsumoGlobal.objects.filter(nome_normalizado=normalizar(nome)).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Já existe um insumo padrão com este nome (acento e maiúsculas são ignorados).")
        return nome
