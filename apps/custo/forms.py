from django import forms
from django.db.models import Q

from web_project.forms import MoedaCentavosField

from .models import CustoModelo, CustoOperacional

CONTROLE = {"class": "form-control"}
LIMITE_VALOR = 10**12  # centavos: teto de segurança


class CustoForm(forms.ModelForm):
    valor = MoedaCentavosField(
        label="Valor mensal (R$)",
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 350,00", "data-mask": "moeda"}),
    )

    class Meta:
        model = CustoOperacional
        fields = ["modelo", "nome", "tipo", "valor", "descricao", "ativo"]
        widgets = {
            "modelo": forms.Select(attrs={"class": "form-select"}),
            "nome": forms.TextInput(attrs=CONTROLE),
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs=CONTROLE),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Custos comuns disponíveis: os padrões do sistema e os PRÓPRIOS da unidade que ela AINDA
        # não usa (cada um, no máximo uma vez); ao editar, o atual continua selecionável.
        usados = CustoOperacional.objects.filter(cliente=self.cliente, modelo__isnull=False).values_list("modelo_id", flat=True)
        filtro = Q(ativo=True) & (Q(cliente__isnull=True) | Q(cliente=self.cliente)) & ~Q(pk__in=list(usados))
        if self.instance.pk and self.instance.modelo_id:
            filtro |= Q(pk=self.instance.modelo_id)
        self.fields["modelo"].queryset = CustoModelo.objects.filter(filtro)
        self.fields["modelo"].empty_label = "Custo próprio (sem modelo)"
        self.fields["modelo"].required = False
        self.fields["nome"].required = False  # com um custo comum, o nome padrão vem do catálogo

    def clean_valor(self):
        valor = self.cleaned_data.get("valor")
        if valor is not None and valor > LIMITE_VALOR:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor or 0

    def clean(self):
        dados = super().clean()
        modelo = dados.get("modelo")
        nome = (dados.get("nome") or "").strip()
        if modelo:
            nome = modelo.nome  # com um custo comum, o nome é SEMPRE o dele (o campo Nome fica oculto na tela)
            dados["tipo"] = modelo.tipo  # o tipo do custo comum é do catálogo
        if not nome:
            self.add_error("nome", "Informe o nome do custo ou escolha um custo comum.")
        elif CustoOperacional.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.pk).exists():
            self.add_error("nome", "Já existe um custo com este nome.")
        dados["nome"] = nome
        return dados


class CustoComumForm(forms.ModelForm):
    """Custo comum PRÓPRIO da unidade (os padrões do sistema não passam por aqui)."""

    class Meta:
        model = CustoModelo
        fields = ["nome", "tipo", "descricao", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={**CONTROLE, "maxlength": 100}),
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "descricao": forms.TextInput(attrs={**CONTROLE, "maxlength": 255}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente

    def clean_nome(self):
        nome = " ".join((self.cleaned_data.get("nome") or "").split())
        if not nome:
            raise forms.ValidationError("Informe o nome do custo comum.")
        # Não pode repetir um custo comum PRÓPRIO nem um padrão do sistema (ficariam ambíguos no seletor).
        repetido = CustoModelo.objects.filter(Q(cliente=self.cliente) | Q(cliente__isnull=True), nome__iexact=nome).exclude(pk=self.instance.pk)  # cliente None = padrão do sistema
        if repetido.exists():
            raise forms.ValidationError("Já existe um custo comum com este nome.")
        return nome
