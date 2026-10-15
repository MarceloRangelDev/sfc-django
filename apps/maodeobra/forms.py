from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.produto.models import Produto
from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin

from .models import Apontamento, Colaborador, ConfiguracaoMaoDeObra

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
HORA = forms.TimeInput(attrs={**CONTROLE, "type": "time"}, format="%H:%M")


class ColaboradorForm(UnidadeFormMixin, forms.ModelForm):
    valor_hora = MoedaCentavosField(label="Valor da hora (R$)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "0,00"}))
    valor_dia = MoedaCentavosField(required=False, label="Valor do dia (R$)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda", "placeholder": "Vazio = valor da hora × jornada"}))

    class Meta:
        model = Colaborador
        fields = ["nome", "funcao", "valor_hora", "valor_dia", "ativo"]
        widgets = {"nome": forms.TextInput(attrs=CONTROLE), "funcao": forms.TextInput(attrs=CONTROLE), "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"})}

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)

    def clean_valor_hora(self):
        valor = self.cleaned_data.get("valor_hora")
        if not valor:
            raise forms.ValidationError("Informe o valor da hora (maior que zero).")
        return valor

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome.")
        repetido = Colaborador.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError("Já existe um colaborador com este nome.")
        return nome


class ApontamentoForm(forms.ModelForm):
    """Apontamento por hora (início/fim/pausa) ou por dia. O cálculo é feito em `Apontamento.clean` no servidor."""

    dias = DecimalBRField(required=False, max_digits=4, decimal_places=1, min_value=Decimal("0.5"), max_value=Decimal("31"), label="Dias trabalhados", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal", "placeholder": "Ex.: 1 ou 0,5"}))
    pausa_minutos = forms.IntegerField(required=False, min_value=0, max_value=1440, initial=0, label="Pausa (minutos)", widget=forms.NumberInput(attrs={**CONTROLE, "min": 0}))

    class Meta:
        model = Apontamento
        fields = ["colaborador", "servico", "descricao", "data", "tipo", "inicio", "fim", "pausa_minutos", "dias", "observacao"]
        widgets = {
            "colaborador": forms.Select(attrs=SELECT), "servico": forms.Select(attrs=SELECT), "descricao": forms.TextInput(attrs=CONTROLE),
            "data": forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"), "tipo": forms.RadioSelect,
            "inicio": HORA, "fim": HORA, "observacao": forms.TextInput(attrs={**CONTROLE, "maxlength": 255}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        escopo = unidades if unidades is not None else type(cliente).objects.filter(pk=getattr(cliente, "pk", None))
        filtro = Q(cliente__in=escopo, ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.colaborador_id)
        self.fields["colaborador"].queryset = Colaborador.objects.filter(filtro).order_by("nome")
        self.fields["colaborador"].empty_label = "Selecione..."
        filtro_servico = Q(cliente__in=escopo, ativo=True)
        if self.instance.pk and self.instance.servico_id:
            filtro_servico |= Q(pk=self.instance.servico_id)
        self.fields["servico"].queryset = Produto.objects.filter(filtro_servico).order_by("nome")
        self.fields["servico"].empty_label = "— Sem serviço (usar a descrição) —"
        self.fields["servico"].required = False
        self.fields["descricao"].required = False
        self.fields["tipo"].choices = Apontamento.TIPO_CHOICES
        self.fields["pausa_minutos"].initial = 0

    def clean_pausa_minutos(self):
        return self.cleaned_data.get("pausa_minutos") or 0


class ConfiguracaoForm(forms.ModelForm):
    jornada_horas = DecimalBRField(max_digits=4, decimal_places=2, min_value=Decimal("0.5"), max_value=Decimal("24"), label="Jornada normal (horas por dia)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}))
    extra_percentual = DecimalBRField(max_digits=6, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("300"), label="Adicional de hora extra (%)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}))
    adicional_domingo = DecimalBRField(max_digits=6, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("300"), label="Adicional de domingo (%)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}))

    class Meta:
        model = ConfiguracaoMaoDeObra
        fields = ["arredondamento", "jornada_horas", "extra_percentual", "adicional_domingo"]
        widgets = {"arredondamento": forms.Select(attrs=SELECT)}

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)


class PeriodoForm(forms.Form):
    """Filtro do relatório (folha): período e colaborador."""

    inicio = forms.DateField(required=False, label="De", widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"))
    fim = forms.DateField(required=False, label="Até", widget=forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"))
    colaborador = forms.ModelChoiceField(queryset=Colaborador.objects.none(), required=False, empty_label="Todos", label="Colaborador", widget=forms.Select(attrs=SELECT))

    def __init__(self, *args, escopo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["colaborador"].queryset = Colaborador.objects.filter(cliente__in=escopo).order_by("nome") if escopo is not None else Colaborador.objects.none()

    def clean(self):
        dados = super().clean()
        if dados.get("inicio") and dados.get("fim") and dados["inicio"] > dados["fim"]:
            raise forms.ValidationError("A data inicial não pode ser posterior à final.")
        return dados
