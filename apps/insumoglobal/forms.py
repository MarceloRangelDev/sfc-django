from django import forms

from apps.medida.models import Medida
from apps.modulo.models import Segmento

from .models import InsumoGlobal, normalizar


class InsumoGlobalForm(forms.ModelForm):
    # Marca opcional: o MESMO nome padrão pode existir com várias marcas (cada marca é uma variação com foto própria).
    marca = forms.CharField(required=False, max_length=60, label="Marca", widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Opcional (ex.: União)"}))
    existente = None  # padrão já cadastrado com este nome, quando a intenção é só acrescentar uma marca a ele

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
        return nome

    def clean_marca(self):
        return " ".join((self.cleaned_data.get("marca") or "").split())

    def clean(self):
        dados = super().clean()
        nome = dados.get("nome")
        if nome:
            repetido = InsumoGlobal.objects.filter(nome_normalizado=normalizar(nome)).exclude(pk=self.instance.pk).first()
            if repetido is not None:
                if self.instance.pk is None and dados.get("marca"):
                    self.existente = repetido  # novo cadastro com marca: vira uma nova marca do padrão que já existe
                else:
                    self.add_error("nome", "Já existe um insumo padrão com este nome. Para cadastrar outra marca do mesmo insumo, informe a marca.")
        return dados
