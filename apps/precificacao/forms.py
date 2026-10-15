from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.canal.models import CanalVenda
from apps.ficha.models import Ficha
from web_project.forms import DecimalBRField, MoedaCentavosField

from .models import Precificacao
from .services import custo_operacional_automatico

CONTROLE = {"class": "form-control"}
LIMITE_CENTAVOS = 10**12  # R$ 10 bilhões: teto de segurança para valores digitados


class PrecificacaoForm(forms.ModelForm):
    """Dados digitados da precificação. O custo da ficha, as taxas do canal
    cadastrado e todos os resultados vêm do servidor (services.preencher)."""

    taxa_percentual = DecimalBRField(
        required=False, max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("99.9999"),
        label="Taxas em porcentagem (%)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 27,79", "data-mask": "decimal", "data-prev": "1"}),
        error_messages={"invalid": "Informe um percentual válido.", "max_value": "O percentual deve ser menor que 100."},
    )
    taxa_fixa = MoedaCentavosField(
        required=False, label="Taxas em reais (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 1,50", "data-mask": "moeda", "data-prev": "1"}),
    )
    # Em branco = automático: usa o que a distribuição do Custo operacional atribui à categoria do produto.
    custo_operacional = MoedaCentavosField(
        required=False, label="Custo operacional da categoria (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 2,00", "data-mask": "moeda", "data-prev": "1"}),
    )
    percentual_lucro = DecimalBRField(
        required=False, max_digits=9, decimal_places=4, min_value=Decimal("0"), label="Margem ou markup (%)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 30", "data-mask": "decimal", "data-prev": "1"}),
        error_messages={"invalid": "Informe um percentual válido."},
    )
    valor_lucro = MoedaCentavosField(
        required=False, label="Lucro em reais (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 5,00", "data-mask": "moeda", "data-prev": "1"}),
    )

    class Meta:
        model = Precificacao
        fields = [
            "ficha", "canal", "canal_nome", "canal_descricao", "taxa_percentual", "taxa_fixa",
            "custo_operacional", "metodo_lucro", "percentual_lucro", "valor_lucro", "usar_no_pdv", "ativo",
        ]
        widgets = {
            "ficha": forms.Select(attrs={"class": "form-select", "data-prev": "1"}),
            "canal": forms.Select(attrs={"class": "form-select", "data-prev": "1"}),
            "canal_nome": forms.TextInput(attrs={**CONTROLE, "data-prev": "1"}),
            "canal_descricao": forms.TextInput(attrs={**CONTROLE, "data-prev": "1"}),
            "metodo_lucro": forms.Select(attrs={"class": "form-select", "data-prev": "1"}),
            "usar_no_pdv": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Fichas e canais ATIVOS da mesma unidade (o campo rejeita ids de fora);
        # ao editar, os atuais continuam válidos mesmo se desativados depois.
        filtro_ficha, filtro_canal = Q(cliente=self.cliente, ativo=True), Q(cliente=self.cliente, ativo=True)
        if self.instance.pk:
            filtro_ficha |= Q(pk=self.instance.ficha_id)
            filtro_canal |= Q(pk=self.instance.canal_id)
        self.fields["ficha"].queryset = Ficha.objects.filter(filtro_ficha).select_related("produto").order_by("nome")
        self.fields["ficha"].label_from_instance = lambda f: f"{f.nome} — {f.produto.nome}"
        self.fields["ficha"].empty_label = "Selecione..."
        self.fields["canal"].queryset = CanalVenda.objects.filter(filtro_canal).order_by("nome", "descricao")
        self.fields["canal"].empty_label = "Personalizado (informar abaixo)"
        self.fields["canal"].label_from_instance = str
        self.fields["canal_nome"].required = False
        self.fields["canal_descricao"].required = False
        self.fields["metodo_lucro"].choices = Precificacao.METODO_CHOICES

    def clean(self):
        dados = super().clean()
        if self.errors:
            return dados
        # Valores vazios viram zero (campos opcionais em reais/percentual).
        for campo in ("taxa_percentual", "percentual_lucro"):
            dados[campo] = dados.get(campo) if dados.get(campo) is not None else Decimal("0")
        informado = dados.get("custo_operacional")
        for campo in ("taxa_fixa", "custo_operacional", "valor_lucro"):
            dados[campo] = dados.get(campo) or 0
            if dados[campo] > LIMITE_CENTAVOS:
                self.add_error(campo, "Valor acima do limite permitido.")
        # Custo operacional: em branco ou igual ao da distribuição = automático (acompanha os custos);
        # qualquer outro valor é um ajuste do usuário e fica marcado como manual.
        ficha = dados.get("ficha")
        if ficha is not None:
            automatico = custo_operacional_automatico(ficha)
            if informado is None:
                dados["custo_operacional"] = automatico
            self.instance.custo_operacional_manual = dados["custo_operacional"] != automatico

        if dados.get("canal"):
            # Canal cadastrado: as taxas e o nome vêm dele; o digitado é descartado.
            dados.update(canal_nome="", canal_descricao="", taxa_percentual=Decimal("0"), taxa_fixa=0)
        elif not (dados.get("canal_nome") or "").strip():
            self.add_error("canal_nome", "Informe o nome do canal personalizado ou escolha um canal cadastrado.")
        else:
            dados["canal_nome"] = dados["canal_nome"].strip()
            dados["canal_descricao"] = (dados.get("canal_descricao") or "").strip()

        metodo = dados.get("metodo_lucro")
        if metodo == Precificacao.METODO_MARGEM and dados["percentual_lucro"] >= 100:
            self.add_error("percentual_lucro", "A margem deve ser menor que 100%.")
        return dados
