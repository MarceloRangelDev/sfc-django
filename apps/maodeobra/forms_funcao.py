"""Formulários de funções, itens de custo, personalização do colaborador, simulações e filtros do dashboard.

A API usa os mesmos formulários (apps/api/maodeobra.py), então toda validação mora aqui e em models_funcao.py/custos.py."""
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin

from . import custos
from .models import Colaborador, ComponenteFuncao, FuncaoMaoDeObra, PersonalizacaoColaborador, SimulacaoPromocao

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
DATA = lambda: forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d")  # noqa: E731
PERCENTUAL = lambda: forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"})  # noqa: E731


def _escopo(cliente, unidades):
    """Unidades alcançáveis: o escopo do usuário (tela) ou só a unidade informada (API)."""
    return unidades if unidades is not None else type(cliente).objects.filter(pk=getattr(cliente, "pk", None))


class FuncaoForm(UnidadeFormMixin, forms.ModelForm):
    class Meta:
        model = FuncaoMaoDeObra
        fields = ["nome", "descricao", "cbo_codigo", "cbo_descricao", "observacao", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "descricao": forms.TextInput(attrs=CONTROLE),
            "cbo_codigo": forms.TextInput(attrs={**CONTROLE, "inputmode": "numeric", "maxlength": 6, "placeholder": "6 dígitos"}),
            "cbo_descricao": forms.TextInput(attrs=CONTROLE), "observacao": forms.TextInput(attrs=CONTROLE),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome da função.")
        if FuncaoMaoDeObra.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Já existe uma função com este nome.")
        return nome


class ComponenteForm(forms.ModelForm):
    """Item de custo de UMA função. A função (e portanto a unidade) vem da URL/instância, nunca do corpo da requisição."""

    valor = MoedaCentavosField(required=False, label="Valor (R$)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "0,00"}))
    percentual = DecimalBRField(required=False, max_digits=8, decimal_places=4, min_value=Decimal("0"), max_value=custos.LIMITE_PERCENTUAL, label="Percentual (%)", widget=PERCENTUAL())

    class Meta:
        model = ComponenteFuncao
        fields = ["categoria", "nome", "tipo_valor", "valor", "percentual", "base", "periodicidade", "personalizavel", "condicao", "inicio", "fim", "ativo"]
        widgets = {
            "categoria": forms.Select(attrs=SELECT), "nome": forms.TextInput(attrs=CONTROLE), "tipo_valor": forms.Select(attrs=SELECT),
            "base": forms.Select(attrs=SELECT), "periodicidade": forms.Select(attrs=SELECT), "condicao": forms.TextInput(attrs=CONTROLE),
            "inicio": DATA(), "fim": DATA(), "personalizavel": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, funcao=None, **kwargs):
        super().__init__(*args, **kwargs)
        if funcao is not None and self.instance.funcao_id is None:
            self.instance.funcao = funcao
        if not self.instance.pk:
            self.fields["inicio"].initial = timezone.localdate()
        # Base e periodicidade só valem para um dos tipos; vazios assumem o padrão (ver `clean`).
        self.fields["base"].required = self.fields["periodicidade"].required = False

    def clean(self):
        dados = super().clean()
        dados["base"] = dados.get("base") or custos.B_SALARIO
        dados["periodicidade"] = dados.get("periodicidade") or custos.MENSAL
        # Campos que não se aplicam ao tipo escolhido são ignorados (a tela os esconde), não viram erro.
        if dados.get("tipo_valor") == custos.FIXO:
            dados["percentual"] = None
        elif dados.get("tipo_valor") == custos.PERCENTUAL:
            dados["valor"] = None
            dados["periodicidade"] = custos.MENSAL
        if dados.get("categoria") == custos.SALARIO and dados.get("tipo_valor") == custos.FIXO:
            dados["periodicidade"] = custos.MENSAL
        if dados.get("tipo_valor") == custos.FIXO:
            dados["base"] = custos.B_SALARIO  # sem efeito em valor fixo; mantém o padrão do banco
        return dados


class PersonalizacaoForm(forms.Form):
    """Valores individuais dos itens PERSONALIZÁVEIS do vínculo (exceto o salário, que é o do cadastro do colaborador).
    Campo vazio = herda o valor da função. Só existem campos para itens marcados como personalizáveis."""

    def __init__(self, *args, vinculo=None, componentes=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.vinculo = vinculo
        self.componentes = [c for c in componentes if c.personalizavel and c.categoria != custos.SALARIO]
        atuais = {p.componente_id: p for p in PersonalizacaoColaborador.objects.filter(vinculo=vinculo)} if vinculo else {}
        for c in self.componentes:
            nome = f"item_{c.pk}"
            if c.tipo_valor == custos.FIXO:
                self.fields[nome] = MoedaCentavosField(required=False, label=c.nome, widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "Herdar"}))
                inicial = atuais[c.pk].valor if c.pk in atuais else None
            else:
                self.fields[nome] = DecimalBRField(required=False, max_digits=8, decimal_places=4, min_value=Decimal("0"), max_value=custos.LIMITE_PERCENTUAL, label=c.nome, widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal", "placeholder": "Herdar"}))
                inicial = atuais[c.pk].percentual if c.pk in atuais else None
            self.initial[nome] = inicial

    def salvar(self):
        """Grava as personalizações preenchidas e remove as esvaziadas (volta a herdar). Valida cada uma pelo modelo."""
        for c in self.componentes:
            valor = self.cleaned_data.get(f"item_{c.pk}")
            existente = PersonalizacaoColaborador.objects.filter(vinculo=self.vinculo, componente=c).first()
            if valor is None:
                if existente:
                    existente.delete()
                continue
            p = existente or PersonalizacaoColaborador(vinculo=self.vinculo, componente=c)
            if c.tipo_valor == custos.FIXO:
                p.valor, p.percentual = valor, None
            else:
                p.valor, p.percentual = None, valor
            p.full_clean()
            p.save()


class SimulacaoForm(UnidadeFormMixin, forms.ModelForm):
    novo_salario = MoedaCentavosField(required=False, label="Novo salário mensal (R$)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "Opcional"}))
    ajuste_salario_percentual = DecimalBRField(required=False, max_digits=7, decimal_places=2, min_value=Decimal("-99.99"), max_value=Decimal("1000"), label="Ajuste do salário (%)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal", "placeholder": "Ex.: 10 ou -5"}))

    class Meta:
        model = SimulacaoPromocao
        fields = ["nome", "tipo", "colaborador", "funcao_origem", "funcao_destino", "novo_salario", "ajuste_salario_percentual", "data_referencia", "observacao"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "tipo": forms.RadioSelect, "colaborador": forms.Select(attrs=SELECT), "funcao_origem": forms.Select(attrs=SELECT),
            "funcao_destino": forms.Select(attrs=SELECT), "data_referencia": DATA(), "observacao": forms.TextInput(attrs=CONTROLE),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        escopo = _escopo(cliente, unidades)
        filtro_colab, filtro_funcao = Q(cliente__in=escopo, ativo=True), Q(cliente__in=escopo, ativo=True)
        if self.instance.pk:  # o já escolhido segue válido mesmo se foi desativado depois
            filtro_colab |= Q(pk=self.instance.colaborador_id)
            filtro_funcao |= Q(pk__in=[self.instance.funcao_origem_id, self.instance.funcao_destino_id])
        self.fields["colaborador"].queryset = Colaborador.objects.filter(filtro_colab).order_by("nome")
        self.fields["funcao_origem"].queryset = self.fields["funcao_destino"].queryset = FuncaoMaoDeObra.objects.filter(filtro_funcao).order_by("nome")
        self.fields["colaborador"].empty_label = "Selecione..."
        self.fields["funcao_origem"].empty_label = "Selecione..."
        self.fields["funcao_destino"].empty_label = "— Mesma função (só ajustar o salário) —"
        for campo in ("colaborador", "funcao_origem", "funcao_destino", "observacao"):
            self.fields[campo].required = False
        self.fields["tipo"].choices = SimulacaoPromocao.TIPOS
        if not self.instance.pk:
            self.fields["data_referencia"].initial = timezone.localdate()

    def clean(self):
        dados = super().clean()
        # O model valida a unidade dos objetos escolhidos; ele precisa conhecer a unidade antes de ser validado.
        self.instance.cliente = self.cliente_alvo()
        return dados


class DashboardForm(forms.Form):
    """Filtros do dashboard (GET). Funções e unidades vêm do escopo do usuário."""

    data = forms.DateField(required=False, label="Data de referência", widget=DATA())
    comparar_com = forms.DateField(required=False, label="Comparar com", widget=DATA())
    funcao = forms.ModelChoiceField(queryset=FuncaoMaoDeObra.objects.none(), required=False, empty_label="Todas", label="Função", widget=forms.Select(attrs=SELECT))
    periodo = forms.ChoiceField(choices=custos.PERIODOS, initial="mes", required=False, label="Período exibido", widget=forms.Select(attrs=SELECT))

    def __init__(self, *args, escopo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["funcao"].queryset = FuncaoMaoDeObra.objects.filter(cliente__in=escopo).order_by("nome") if escopo is not None else FuncaoMaoDeObra.objects.none()

    def clean(self):
        dados = super().clean()
        dados["data"] = dados.get("data") or timezone.localdate()
        dados["periodo"] = dados.get("periodo") or "mes"
        return dados
