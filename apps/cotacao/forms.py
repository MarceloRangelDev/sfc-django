import re
from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.auditoria.utils import tirar_foto
from apps.insumo.models import Insumo
from apps.medida.models import Medida
from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin

from .models import LIMITE_OBSERVACAO_GERAL, LIMITE_OBSERVACAO_ITEM, Cotacao, CotacaoItem, CotacaoLoja, CotacaoPreco

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
LIMITE_QUANTIDADE = Decimal("100000000000")  # cabe em Decimal(15,4)
LIMITE_CENTAVOS = 10**13


def _quantidade(label, obrigatoria=True):
    return DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), max_value=LIMITE_QUANTIDADE, label=label, required=obrigatoria,
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "A quantidade deve ser maior que zero.", "max_value": "Quantidade acima do limite permitido."},
    )


def _preco(label, obrigatorio=True):
    return MoedaCentavosField(
        label=label, required=obrigatorio,
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "0,00"}),
        error_messages={"invalid": "Informe um valor válido.", "min_value": "O valor não pode ser negativo."},
    )


class _FotoAntesMixin:
    """Guarda a foto do registro ANTES de o formulário alterá-lo (a auditoria compara antes/depois)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.foto_antes = tirar_foto(self.instance) if self.instance.pk else None


class CotacaoForm(_FotoAntesMixin, UnidadeFormMixin, forms.ModelForm):
    class Meta:
        model = Cotacao
        fields = ["titulo", "observacoes"]
        widgets = {
            "titulo": forms.TextInput(attrs=CONTROLE),
            "observacoes": forms.Textarea(attrs={**CONTROLE, "rows": 3, "maxlength": LIMITE_OBSERVACAO_GERAL}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)

    def clean_titulo(self):
        titulo = " ".join((self.cleaned_data.get("titulo") or "").split())
        if not titulo:
            raise forms.ValidationError("Informe o título da cotação.")
        return titulo

    def clean_observacoes(self):
        return "\n".join((self.cleaned_data.get("observacoes") or "").splitlines()).strip()


class LojaForm(_FotoAntesMixin, forms.ModelForm):
    class Meta:
        model = CotacaoLoja
        fields = ["nome", "endereco", "telefone", "email"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "endereco": forms.TextInput(attrs=CONTROLE),
            "telefone": forms.TextInput(attrs={**CONTROLE, "data-mask": "fone", "placeholder": "(11) 99999-9999"}),
            "email": forms.EmailInput(attrs=CONTROLE),
        }

    def __init__(self, *args, cotacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cotacao = cotacao or self.instance.cotacao
        self.instance.cotacao = self.cotacao
        for campo in ("endereco", "telefone", "email"):
            self.fields[campo].required = False  # opcionais

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome da loja.")
        repetida = CotacaoLoja.objects.filter(cotacao=self.cotacao, nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError("Esta loja já está na cotação.")
        return nome

    def clean_telefone(self):
        digitos = re.sub(r"\D", "", self.cleaned_data.get("telefone") or "")
        if digitos and not 10 <= len(digitos) <= 11:
            raise forms.ValidationError("Informe DDD + número (10 ou 11 dígitos).")
        return digitos

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()


class ItemForm(_FotoAntesMixin, forms.ModelForm):
    quantidade = _quantidade("Quantidade")

    class Meta:
        model = CotacaoItem
        fields = ["insumo", "descricao", "quantidade", "medida", "servico", "observacao"]
        widgets = {
            "insumo": forms.Select(attrs=SELECT),
            "descricao": forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex.: Manutenção do forno (serviço)"}),
            "medida": forms.Select(attrs=SELECT),
            "servico": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "observacao": forms.TextInput(attrs={**CONTROLE, "maxlength": LIMITE_OBSERVACAO_ITEM}),
        }

    def __init__(self, *args, cotacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cotacao = cotacao or self.instance.cotacao
        self.instance.cotacao = self.cotacao
        filtro = Q(cliente_id=self.cotacao.cliente_id, ativo=True)
        if self.instance.pk and self.instance.insumo_id:
            filtro |= Q(pk=self.instance.insumo_id)
        self.fields["insumo"].queryset = Insumo.objects.filter(filtro).select_related("medida").order_by("nome")
        self.fields["insumo"].empty_label = "— Item avulso / serviço —"
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nome} ({i.medida.sigla})"
        self.fields["insumo"].required = False
        self.fields["descricao"].required = False
        filtro_medida = Q(ativo=True) | (Q(pk=self.instance.medida_id) if self.instance.pk else Q())
        self.fields["medida"].queryset = Medida.objects.filter(filtro_medida).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."

    def clean(self):
        dados = super().clean()
        insumo, medida = dados.get("insumo"), dados.get("medida")
        descricao = " ".join((dados.get("descricao") or "").split())
        if insumo is not None:
            if medida is not None and medida.dimensao != insumo.medida.dimensao:
                self.add_error("medida", "A unidade deve ser da mesma dimensão da unidade do insumo.")
            repetido = CotacaoItem.objects.filter(cotacao=self.cotacao, insumo=insumo).exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error("insumo", "Este insumo já está na cotação.")
            descricao = insumo.nome
            dados["servico"] = False
        elif not descricao:
            self.add_error("descricao", "Informe o item (ou escolha um insumo).")
        dados["descricao"] = descricao
        return dados


class PrecoForm(_FotoAntesMixin, forms.ModelForm):
    quantidade = _quantidade("Quantidade")
    preco_unitario = _preco("Preço unitário (R$ por unidade)")
    preco_atacado = _preco("Preço no atacado (R$ por unidade)", obrigatorio=False)
    qtd_minima_atacado = _quantidade("Quantidade mínima para atacado", obrigatoria=False)

    class Meta:
        model = CotacaoPreco
        fields = ["loja", "marca", "medida", "quantidade", "preco_unitario", "preco_atacado", "preco_e_atacado", "qtd_minima_atacado", "observacao"]
        widgets = {
            "loja": forms.Select(attrs=SELECT),
            "marca": forms.TextInput(attrs={**CONTROLE, "maxlength": 60}),
            "medida": forms.Select(attrs=SELECT),
            "preco_e_atacado": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "observacao": forms.TextInput(attrs={**CONTROLE, "maxlength": LIMITE_OBSERVACAO_ITEM}),
        }

    def __init__(self, *args, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item or self.instance.item
        self.instance.item = self.item
        # Só lojas DESTA cotação (o campo rejeita ids de fora, inclusive de outro tenant).
        self.fields["loja"].queryset = CotacaoLoja.objects.filter(cotacao_id=self.item.cotacao_id).order_by("nome")
        self.fields["loja"].empty_label = "Selecione..."
        filtro_medida = Q(ativo=True, dimensao=self.item.medida.dimensao) | (Q(pk=self.instance.medida_id) if self.instance.pk else Q())
        self.fields["medida"].queryset = Medida.objects.filter(filtro_medida).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."
        self.fields["marca"].required = False  # obrigatória exceto para serviços (clean)

    def clean_marca(self):
        return " ".join((self.cleaned_data.get("marca") or "").split())

    def clean_preco_unitario(self):
        valor = self.cleaned_data.get("preco_unitario")
        if valor is not None and not 0 < valor <= LIMITE_CENTAVOS:
            raise forms.ValidationError("Informe um preço maior que zero.")
        return valor

    def clean_preco_atacado(self):
        valor = self.cleaned_data.get("preco_atacado")
        if valor is not None and not 0 < valor <= LIMITE_CENTAVOS:
            raise forms.ValidationError("Informe um preço de atacado maior que zero.")
        return valor

    def clean(self):
        dados = super().clean()
        loja, medida = dados.get("loja"), dados.get("medida")
        if medida is not None and medida.dimensao != self.item.medida.dimensao:
            self.add_error("medida", "A unidade deve ser da mesma dimensão da unidade do item.")
        if loja is not None:
            repetido = CotacaoPreco.objects.filter(item=self.item, loja=loja).exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error("loja", "Esta loja já tem preço para este item; edite o existente.")
        if not self.item.servico and not dados.get("marca"):
            self.add_error("marca", "Informe a marca (obrigatória, exceto para serviços).")
        atacado, minimo, e_atacado, unitario = dados.get("preco_atacado"), dados.get("qtd_minima_atacado"), dados.get("preco_e_atacado"), dados.get("preco_unitario")
        if e_atacado:
            if atacado is not None:
                self.add_error("preco_atacado", "Se o preço informado já é de atacado, deixe o preço no atacado em branco.")
        else:
            if atacado is not None and minimo is None:
                self.add_error("qtd_minima_atacado", "Informe a quantidade mínima para o preço de atacado.")
            if minimo is not None and atacado is None:
                self.add_error("preco_atacado", "Informe o preço de atacado (ou marque que o preço informado é de atacado).")
            if atacado is not None and unitario is not None and atacado > unitario:
                self.add_error("preco_atacado", "O preço de atacado não pode ser maior que o preço unitário.")
        return dados


class ImportarListaForm(forms.Form):
    """Seleção de itens da lista de compras a copiar para a cotação (ids validados na view contra a unidade)."""

    itens = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, disponiveis=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["itens"].choices = [(str(i.pk), f"{i.descricao} ({i.quantidade.normalize():f} {i.medida.sigla})") for i in disponiveis or []]


class ContaPagarForm(forms.Form):
    """Parâmetros para gerar as contas a pagar dos vencedores (a categoria é validada de verdade em services)."""

    categoria = forms.IntegerField(label="Categoria (despesa)", widget=forms.Select(attrs=SELECT))
    vencimento = forms.DateField(label="Vencimento", widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, categorias=None, **kwargs):
        super().__init__(*args, **kwargs)
        opcoes = [("", "Selecione...")] + [(c.pk, c.nome) for c in categorias or []]
        self.fields["categoria"].widget = forms.Select(attrs=SELECT, choices=opcoes)
