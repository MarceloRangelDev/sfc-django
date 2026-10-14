from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.insumo.models import Insumo
from apps.medida.models import Medida
from web_project.forms import DecimalBRField, UnidadeFormMixin

from .models import ItemCompra

CONTROLE = {"class": "form-control"}
LIMITE_QUANTIDADE = Decimal("100000000000")  # cabe em Decimal(15,4)


class ItemCompraForm(UnidadeFormMixin, forms.ModelForm):
    """Item da lista de compras: de um insumo (nome copiado dele) ou avulso (texto livre/serviço)."""

    quantidade = DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), max_value=LIMITE_QUANTIDADE, label="Quantidade",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "A quantidade deve ser maior que zero.", "max_value": "Quantidade acima do limite permitido."},
    )

    class Meta:
        model = ItemCompra
        fields = ["insumo", "descricao", "quantidade", "medida", "observacao"]
        widgets = {
            "insumo": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex.: Manutenção do forno (serviço)"}),
            "medida": forms.Select(attrs={"class": "form-select"}),
            "observacao": forms.TextInput(attrs={**CONTROLE, "maxlength": 255}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        # Insumos: só ATIVOS das unidades que o usuário alcança (o campo rejeita ids de fora; clean() confere a unidade escolhida).
        escopo = unidades if unidades is not None else type(cliente).objects.filter(pk=getattr(cliente, "pk", None))
        filtro = Q(cliente__in=escopo, ativo=True)
        if self.instance.pk and self.instance.insumo_id:
            filtro |= Q(pk=self.instance.insumo_id)
        self.fields["insumo"].queryset = Insumo.objects.filter(filtro).select_related("medida").order_by("nome")
        self.fields["insumo"].empty_label = "— Item avulso / serviço —"
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nome} ({i.medida.sigla})"
        self.fields["insumo"].required = False
        self.fields["descricao"].required = False
        filtro_medida = Q(ativo=True)
        if self.instance.pk:
            filtro_medida |= Q(pk=self.instance.medida_id)
        self.fields["medida"].queryset = Medida.objects.filter(filtro_medida).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."

    def clean(self):
        dados = super().clean()
        insumo, medida = dados.get("insumo"), dados.get("medida")
        descricao = " ".join((dados.get("descricao") or "").split())
        if insumo is not None:
            if insumo.cliente_id != self.cliente_alvo().pk:
                self.add_error("insumo", "O insumo deve ser da mesma unidade do item.")
            elif medida is not None and medida.dimensao != insumo.medida.dimensao:
                self.add_error("medida", "A unidade deve ser da mesma dimensão da unidade do insumo.")
            if self.instance.insumo_id != insumo.pk:  # troca/novo: um item por insumo na lista
                if ItemCompra.objects.filter(cliente=self.cliente_alvo(), insumo=insumo).exclude(pk=self.instance.pk).exists():
                    self.add_error("insumo", "Este insumo já está na lista de compras; edite a quantidade do item existente.")
            descricao = insumo.nome
        elif not descricao:
            self.add_error("descricao", "Informe o item (ou escolha um insumo).")
        dados["descricao"] = descricao
        return dados
