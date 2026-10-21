from django import forms
from django.db.models import Q

from apps.categoria.models import Categoria

from .codigos import PADRAO_CODIGO
from .models import Produto

CONTROLE = {"class": "form-control"}


class ProdutoForm(forms.ModelForm):
    # Tela de Serviços (Mão de obra): o tipo é fixo e não aparece no formulário.
    tipo_fixo = None

    class Meta:
        model = Produto
        fields = ["nome", "categoria", "descricao", "codigo_barras", "tipo", "ativo"]
        widgets = {
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "nome": forms.TextInput(attrs=CONTROLE),
            "categoria": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs=CONTROLE),
            "codigo_barras": forms.TextInput(attrs={**CONTROLE, "maxlength": 32, "autocomplete": "off", "placeholder": "Em branco = gerar automaticamente"}),
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
        # Tipo: fixo pela tela de Serviços ou pelo segmento (só serviço / só produto); escolhido apenas em Serviço e Produto.
        fixo = self.tipo_fixo or Produto.tipo_imposto_pelo_segmento(self.cliente)
        if fixo:
            del self.fields["tipo"]
            self.instance.tipo = fixo
        else:
            self.fields["tipo"].choices = Produto.TIPO_CHOICES

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome do produto é obrigatório.")
        repetido = Produto.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Este cadastro já existe (produtos e serviços compartilham o nome).")
        return nome

    def clean_descricao(self):
        return (self.cleaned_data.get("descricao") or "").strip()

    def clean_codigo_barras(self):
        codigo = (self.cleaned_data.get("codigo_barras") or "").strip()
        if not codigo:
            return self.instance.codigo_barras if self.instance.pk else ""  # em branco: mantém o atual / gera ao salvar
        if not PADRAO_CODIGO.match(codigo):
            raise forms.ValidationError("Use só letras, números, ponto, hífen ou sublinhado (até 32 caracteres).")
        repetido = Produto.objects.filter(cliente=self.cliente, codigo_barras=codigo).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Este código já pertence a outro produto.")
        return codigo


class ServicoForm(ProdutoForm):
    """Cadastro de serviço (tela do módulo Mão de obra): o mesmo formulário de produto com o tipo travado em "serviço"."""

    tipo_fixo = Produto.SERVICO
