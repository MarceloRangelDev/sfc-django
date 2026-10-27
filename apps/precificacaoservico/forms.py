"""Formulários do módulo. Só coletam e convertem campos; a validação de verdade (percentuais, horas, valores) está em calculos.py/services.py,
as mesmas usadas pela API."""
import json
from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.consumidor.models import Consumidor
from web_project.forms import DecimalBRField, MoedaCentavosField
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro

from .models import ConfiguracaoServico, DespesaFixa, Lancamento, ModeloServico, Pagamento, Proposta

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
MOEDA = {**CONTROLE, "data-mask": "moeda", "placeholder": "0,00"}
DECIMAL = {**CONTROLE, "data-mask": "decimal"}


def _moeda(rotulo, **kw):
    return MoedaCentavosField(required=kw.pop("required", False), label=rotulo, widget=forms.TextInput(attrs=MOEDA), **kw)


def _decimal(rotulo, casas=2, digitos=8, **kw):
    return DecimalBRField(required=kw.pop("required", False), label=rotulo, max_digits=digitos, decimal_places=casas, widget=forms.TextInput(attrs=DECIMAL), **kw)


class ConfiguracaoForm(forms.ModelForm):
    remuneracao_mensal = _moeda("Remuneração mensal desejada (R$)")
    custo_variavel_hora = _moeda("Custo variável por hora (R$)")
    dias_mes = _decimal("Dias de trabalho no mês", 1, 4, required=True)
    horas_dia = _decimal("Horas de trabalho por dia", 2, 4, required=True)
    horas_nao_faturaveis = _decimal("Horas não faturáveis no mês", 2, 6)
    horas_faturaveis_informadas = _decimal("Horas faturáveis no mês (se quiser informar)", 2, 6)
    impostos_pct = _decimal("Impostos estimados (% do preço)", 2, 5)
    taxas_pct = _decimal("Taxas estimadas (% do preço)", 2, 5)
    margem_pct = _decimal("Margem de lucro desejada (% do preço)", 2, 5)

    class Meta:
        model = ConfiguracaoServico
        fields = ["moeda", "remuneracao_mensal", "dias_mes", "horas_dia", "horas_nao_faturaveis", "horas_faturaveis_informadas", "custo_variavel_hora", "impostos_pct", "taxas_pct", "margem_pct"]
        widgets = {"moeda": forms.Select(attrs=SELECT, choices=[("BRL", "Real (R$)")])}

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_moeda(self):
        if self.cleaned_data.get("moeda") != "BRL":
            raise forms.ValidationError("Por enquanto só o Real (BRL) está disponível; não há conversão entre moedas.")
        return "BRL"

    def clean(self):
        dados = super().clean()
        for campo in ("remuneracao_mensal", "custo_variavel_hora", "horas_nao_faturaveis", "impostos_pct", "taxas_pct", "margem_pct"):
            if dados.get(campo) is None and campo in self.cleaned_data:
                dados[campo] = Decimal(0) if "pct" in campo or "horas" in campo else 0
        return dados


class DespesaForm(forms.ModelForm):
    valor = _moeda("Valor (R$)", required=True)

    class Meta:
        model = DespesaFixa
        fields = ["natureza", "descricao", "categoria", "valor", "periodicidade", "vigencia_inicio", "ativo"]
        widgets = {
            "natureza": forms.Select(attrs=SELECT), "descricao": forms.TextInput(attrs=CONTROLE), "categoria": forms.Select(attrs=SELECT),
            "periodicidade": forms.Select(attrs=SELECT), "vigencia_inicio": forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vigencia_inicio"].input_formats = ["%Y-%m-%d"]

    def clean_valor(self):
        valor = self.cleaned_data.get("valor")
        if not valor:
            raise forms.ValidationError("Informe um valor maior que zero.")
        return valor


class ModeloForm(forms.ModelForm):
    preco_base = _moeda("Preço-base por unidade (R$)")
    horas_por_unidade = _decimal("Horas por unidade", 2, 7)
    margem_pct = _decimal("Margem própria (%)", 2, 5)

    class Meta:
        model = ModeloServico
        fields = ["nome", "categoria", "modo", "unidade", "preco_base", "horas_por_unidade", "margem_pct", "descricao", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "categoria": forms.TextInput(attrs=CONTROLE), "modo": forms.Select(attrs=SELECT), "unidade": forms.TextInput(attrs=CONTROLE),
            "descricao": forms.Textarea(attrs={**CONTROLE, "rows": 3}), "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome.")
        if ModeloServico.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Já existe um modelo com este nome.")
        return nome


class PropostaForm(forms.ModelForm):
    """Cabeçalho, textos e parâmetros do preço. Itens e custos chegam em campos JSON (`itens_json`, `custos_json`) montados pela tela."""

    preco_fechado = _moeda("Preço fechado (R$)")
    minimo_cobranca = _moeda("Cobrança mínima (R$)")
    acrescimo_valor = _moeda("Acréscimo em valor (R$)")
    margem_pct = _decimal("Margem desta proposta (%)", 2, 5)
    markup_pct = _decimal("Markup (% sobre o custo)", 2, 7)
    horas_minimas = _decimal("Horas mínimas", 2, 6)
    horas_deslocamento = _decimal("Horas de deslocamento", 2, 6)
    acrescimo_pct = _decimal("Acréscimo (%)", 2, 6)
    custo_financeiro_pct = _decimal("Custo financeiro do parcelamento (%)", 2, 5)
    # Em reais quando o tipo é "valor"; em % quando "percentual" (convertido em clean e em prepare_value).
    desconto = _decimal("Desconto", 2, 12)

    class Meta:
        model = Proposta
        fields = [
            "consumidor", "destinatario_nome", "destinatario_email", "destinatario_whatsapp", "descricao", "modo", "validade", "prazo", "condicoes_pagamento",
            "escopo_incluido", "escopo_excluido", "etapas", "materiais_incluidos", "materiais_excluidos", "limites", "regras_extras", "observacoes",
            "metodo_preco", "margem_pct", "markup_pct", "preco_fechado", "minimo_cobranca", "horas_minimas", "arredondamento_minutos", "horas_deslocamento",
            "cobra_deslocamento", "desconto_tipo", "desconto", "acrescimo_pct", "acrescimo_valor", "custo_financeiro_pct", "detalhar_custos",
        ]
        widgets = {
            "consumidor": forms.Select(attrs=SELECT), "destinatario_nome": forms.TextInput(attrs=CONTROLE), "destinatario_email": forms.EmailInput(attrs=CONTROLE),
            "destinatario_whatsapp": forms.TextInput(attrs={**CONTROLE, "placeholder": "+5511999990000", "inputmode": "tel"}),
            "descricao": forms.TextInput(attrs=CONTROLE), "modo": forms.Select(attrs=SELECT),
            "validade": forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), "prazo": forms.TextInput(attrs=CONTROLE),
            "condicoes_pagamento": forms.Textarea(attrs={**CONTROLE, "rows": 2}), "limites": forms.TextInput(attrs=CONTROLE),
            "metodo_preco": forms.Select(attrs=SELECT), "arredondamento_minutos": forms.Select(attrs=SELECT, choices=[(0, "Sem arredondamento"), (15, "15 minutos"), (30, "30 minutos"), (60, "1 hora")]),
            "desconto_tipo": forms.Select(attrs=SELECT), "cobra_deslocamento": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "detalhar_custos": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            **{c: forms.Textarea(attrs={**CONTROLE, "rows": 2}) for c in ("escopo_incluido", "escopo_excluido", "etapas", "materiais_incluidos", "materiais_excluidos", "regras_extras", "observacoes")},
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        filtro = Q(cliente=self.cliente, ativo=True, anonimizado_em__isnull=True)
        if self.instance.pk and self.instance.consumidor_id:
            filtro |= Q(pk=self.instance.consumidor_id)
        self.fields["consumidor"].queryset = Consumidor.objects.filter(filtro).order_by("nome")
        self.fields["consumidor"].required = False
        self.fields["consumidor"].empty_label = "— Digitar os dados abaixo —"
        self.fields["destinatario_nome"].required = False
        self.fields["validade"].input_formats = ["%Y-%m-%d"]
        # O banco guarda desconto em centavos (tipo valor) ou % (tipo percentual); a tela mostra reais/%.
        if self.instance.pk and self.instance.desconto_tipo == "valor":
            self.initial["desconto"] = Decimal(self.instance.desconto) / 100

    def clean_arredondamento_minutos(self):
        valor = self.cleaned_data.get("arredondamento_minutos")
        if valor not in (0, 15, 30, 60):
            raise forms.ValidationError("Escolha um dos arredondamentos da lista.")
        return valor

    def clean(self):
        dados = super().clean()
        consumidor = dados.get("consumidor")
        if consumidor is not None:  # copia do cadastro o que não foi digitado (o cadastro pode mudar ou ser anonimizado depois)
            dados["destinatario_nome"] = (dados.get("destinatario_nome") or consumidor.nome).strip()
            dados["destinatario_email"] = dados.get("destinatario_email") or consumidor.email
            dados["destinatario_whatsapp"] = dados.get("destinatario_whatsapp") or consumidor.whatsapp or consumidor.telefone
        if not (dados.get("destinatario_nome") or "").strip():
            self.add_error("destinatario_nome", "Informe o cliente ou escolha um do cadastro.")
        desconto = dados.get("desconto") or Decimal(0)
        if desconto < 0:
            self.add_error("desconto", "O desconto não pode ser negativo.")
        elif dados.get("desconto_tipo") == "valor":
            dados["desconto"] = desconto * 100  # reais -> centavos
        for campo in ("preco_fechado", "minimo_cobranca", "acrescimo_valor"):
            if dados.get(campo) is None and campo != "preco_fechado":
                dados[campo] = 0
        for campo in ("horas_minimas", "horas_deslocamento", "acrescimo_pct", "custo_financeiro_pct"):
            if dados.get(campo) is None:
                dados[campo] = Decimal(0)
        return dados

    def save(self, commit=True):
        instancia = super().save(commit=False)
        # `desconto` do cleaned_data pode ter mais de 2 casas depois do x100: centavos são inteiros.
        if instancia.desconto_tipo == "valor":
            instancia.desconto = Decimal(instancia.desconto).quantize(Decimal(1))
        if commit:
            instancia.save()
        return instancia


# ---------------------------------------------------------------------------------------------------------------------------
# Linhas (itens e custos) enviadas pela tela como JSON
# ---------------------------------------------------------------------------------------------------------------------------

def _numero(texto, campo):
    try:
        return ler_numero_brasileiro(texto)
    except RegraNegocioError:
        raise RegraNegocioError(f"{campo}: informe um número válido.") from None


def _reais_em_centavos(texto, campo):
    try:
        return DinheiroService.de_texto(texto)
    except RegraNegocioError:
        raise RegraNegocioError(f"{campo}: informe um valor válido.") from None


def linhas_do_json(texto_itens, texto_custos):
    """JSON da tela -> (itens, custos) com Decimal/int de centavos, prontos para `services`. Texto inválido vira RegraNegocioError."""
    try:
        itens_brutos, custos_brutos = json.loads(texto_itens or "[]"), json.loads(texto_custos or "[]")
    except ValueError:
        raise RegraNegocioError("Não foi possível ler os itens enviados. Recarregue a página e tente novamente.") from None
    if not isinstance(itens_brutos, list) or not isinstance(custos_brutos, list) or not all(isinstance(x, dict) for x in itens_brutos + custos_brutos):
        raise RegraNegocioError("Itens e custos em formato inválido.")
    itens, custos = [], []
    for n, i in enumerate(itens_brutos, 1):
        horas = i.get("horas_por_unidade")
        preco = i.get("preco_unitario")
        itens.append({
            "descricao": i.get("descricao"), "modo": i.get("modo"), "unidade": i.get("unidade"),
            "quantidade": _numero(i.get("quantidade"), f"Item {n} — quantidade"),
            "horas_por_unidade": None if horas in (None, "") else _numero(horas, f"Item {n} — horas por unidade"),
            "preco_unitario": None if preco in (None, "") else _reais_em_centavos(preco, f"Item {n} — preço unitário"),
        })
    for n, k in enumerate(custos_brutos, 1):
        custos.append({
            "descricao": k.get("descricao"), "categoria": k.get("categoria"), "unidade": k.get("unidade"), "repasse": k.get("repasse"),
            "quantidade": _numero(k.get("quantidade"), f"Custo {n} — quantidade"),
            "custo_unitario": _reais_em_centavos(k.get("custo_unitario"), f"Custo {n} — custo unitário"),
        })
    return itens, custos


class LancamentoForm(forms.Form):
    tipo = forms.ChoiceField(choices=Lancamento.TIPO_CHOICES, widget=forms.Select(attrs=SELECT))
    data = forms.DateField(widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
    descricao = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=CONTROLE))
    horas = _decimal("Horas", 2, 7)
    custo = _moeda("Custo real (R$)")
    receita = _moeda("Valor cobrado do extra (R$)")


class PagamentoForm(forms.Form):
    valor = _moeda("Valor (R$)", required=True)
    data = forms.DateField(widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
    meio = forms.ChoiceField(choices=Pagamento.MEIO_CHOICES, widget=forms.Select(attrs=SELECT))
    taxa = _moeda("Taxa do meio (R$)")
    status = forms.ChoiceField(choices=[(Pagamento.RECEBIDO, "Recebido"), (Pagamento.PENDENTE, "Pendente")], widget=forms.Select(attrs=SELECT))
    observacao = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs=CONTROLE))


class DatasForm(forms.Form):
    data_inicio = forms.DateField(required=False, widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
    data_conclusao = forms.DateField(required=False, widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
