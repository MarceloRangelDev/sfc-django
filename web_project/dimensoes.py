"""Dimensões do metro (linear, área, volume) compartilhadas por Insumo, item de processado e item de ficha.

A regra de negócio fica em web_project/regras/conversao.py (`quantidade_por_dimensoes`); aqui ficam só o mixin
de colunas e o mixin de formulário, para que as três telas e a API validem do mesmo jeito.
"""
from decimal import Decimal

from django import forms
from django.db import models

from web_project.forms import DecimalBRField
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import DIMENSOES_METRO, medida_e_metro, quantidade_por_dimensoes

CAMPOS_DIMENSOES = ["dimensoes", "comprimento", "largura", "altura"]
SIGLA_POR_DIMENSOES = {1: "m", 2: "m²", 3: "m³"}


def dimensoes_de(form):
    """Dimensões validadas de uma linha (dict para `Modelo(**dimensoes_de(f))`), junto de insumo/medida/quantidade."""
    return {campo: form.cleaned_data.get(campo) for campo in CAMPOS_DIMENSOES}


def texto_decimal(numero):
    """Decimal sem zeros inúteis, com vírgula (2.8800 -> '2,88')."""
    return format(numero.normalize(), "f").replace(".", ",")


class DimensoesMixin(models.Model):
    """Colunas das dimensões. `quantidade` continua sendo o valor usado nas contas: com `dimensoes` > 1 ela é
    derivada (comprimento × largura [× altura]) pelo formulário; registros antigos ficam em 1 dimensão."""

    dimensoes = models.PositiveSmallIntegerField(
        default=1, db_default=1, choices=list(DIMENSOES_METRO.items()), verbose_name="Dimensões",
        help_text="Só para a unidade Metro: 1 = linear, 2 = área, 3 = volume.",
    )
    comprimento = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True, verbose_name="Comprimento (m)")
    largura = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True, verbose_name="Largura (m)")
    altura = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True, verbose_name="Altura (m)")

    class Meta:
        abstract = True

    @property
    def descricao_dimensoes(self):
        """'2,4 × 1,2 m = 2,88 m²' para 2 e 3 dimensões; vazio para linear (a quantidade já é a medida)."""
        if self.dimensoes < 2:
            return ""
        medidas = [self.comprimento, self.largura] + ([self.altura] if self.dimensoes == 3 else [])
        texto = " × ".join(texto_decimal(m) for m in medidas if m is not None)
        return f"{texto} m = {texto_decimal(self.quantidade)} {SIGLA_POR_DIMENSOES[self.dimensoes]}"


    @property
    def quantidade_texto(self):
        """Como a quantidade aparece nas listas: '2,4 × 1,2 m = 2,88 m²' (2 e 3 dimensões) ou '1,5 kg'."""
        return self.descricao_dimensoes or f"{texto_decimal(self.quantidade)} {self.medida.sigla}"


class MedidaSelect(forms.Select):
    """Select de unidade que informa quais opções são o Metro (data-metro-ids="13" no próprio <select>): o JS só mostra
    os campos de dimensões para elas. O HTML das opções não muda."""

    def get_context(self, name, value, attrs):
        contexto = super().get_context(name, value, attrs)
        ids = [
            str(opcao["value"].value) for _grupo, opcoes, _i in contexto["widget"]["optgroups"] for opcao in opcoes
            if getattr(opcao["value"], "instance", None) is not None and medida_e_metro(opcao["value"].instance)
        ]
        contexto["widget"]["attrs"]["data-metro-ids"] = ",".join(ids)
        return contexto


def _decimal_opcional(rotulo, nome):
    return DecimalBRField(
        required=False, max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), label=rotulo,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "data-mask": "decimal", "data-dim": f"campo-{nome}", "placeholder": rotulo}),
        error_messages={"invalid": "Informe um valor válido.", "min_value": "O valor deve ser maior que zero."},
    )


class DimensoesFormMixin:
    """Mixin de ModelForm (com `medida` e `quantidade`): acrescenta os campos de dimensões e valida/deriva a quantidade.

    Chamar `configurar_dimensoes()` no fim do __init__ do formulário (depois de definir o queryset de `medida`) e
    incluir CAMPOS_DIMENSOES em Meta.fields. Sem `dimensoes` no envio (API e clientes antigos) vale 1 dimensão.
    """

    def configurar_dimensoes(self):
        campos = self.fields
        campos["quantidade"].required = False  # obrigatória só em 1 dimensão (conferido em clean)
        campos["quantidade"].widget.attrs["data-dim"] = "quantidade"
        campos["dimensoes"] = forms.TypedChoiceField(
            required=False, coerce=int, empty_value=1, choices=list(DIMENSOES_METRO.items()), label="Dimensões",
            widget=forms.Select(attrs={"class": "form-select form-select-sm", "data-dim": "dimensoes"}),
        )
        for nome, rotulo in (("comprimento", "Comprimento (m)"), ("largura", "Largura (m)"), ("altura", "Altura (m)")):
            campos[nome] = _decimal_opcional(rotulo, nome)
        # a unidade passa a marcar o metro; queryset e rótulos já definidos são mantidos
        atributos = campos["medida"].widget.attrs
        campos["medida"].widget = MedidaSelect(attrs={**atributos, "data-dim": "medida"})
        campos["medida"].widget.choices = campos["medida"].choices

    def clean(self):
        dados = super().clean()
        medida = dados.get("medida")
        dimensoes = dados.get("dimensoes") or 1
        if medida is None or not medida_e_metro(medida):
            dimensoes = 1  # outras unidades não têm dimensões: ignora o que vier
        dados["dimensoes"] = dimensoes
        if dimensoes == 1:
            dados["comprimento"] = dados["largura"] = dados["altura"] = None
            if dados.get("quantidade") is None and "quantidade" not in self.errors:
                self.add_error("quantidade", "Informe uma quantidade válida.")
            return dados
        if dimensoes == 2:
            dados["altura"] = None
        try:
            dados["quantidade"] = quantidade_por_dimensoes(dimensoes, dados.get("comprimento"), dados.get("largura"), dados.get("altura"))
        except RegraNegocioError as erro:
            self.add_error("comprimento", str(erro))
            return dados
        self.errors.pop("quantidade", None)
        return dados
