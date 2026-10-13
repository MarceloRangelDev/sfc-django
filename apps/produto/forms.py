from django import forms
from django.db.models import Q

from apps.categoria.models import Categoria

from .models import Produto

CONTROLE = {"class": "form-control"}


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = ["nome", "categoria", "descricao", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE),
            "categoria": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs=CONTROLE),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Unidade dona do produto: a atual ao editar; ao criar, a do usuário.
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Só categorias ATIVAS da mesma unidade (o campo rejeita ids de fora);
        # ao editar, a categoria atual segue válida mesmo se desativada depois.
        filtro = Q(cliente=self.cliente, ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.categoria_id)
        self.fields["categoria"].queryset = Categoria.objects.filter(filtro).order_by("nome")
        self.fields["categoria"].empty_label = "Selecione..."

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome do produto é obrigatório.")
        repetido = Produto.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Este produto já está cadastrado.")
        return nome

    def clean_descricao(self):
        return (self.cleaned_data.get("descricao") or "").strip()
