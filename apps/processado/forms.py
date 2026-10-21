from decimal import Decimal

from django import forms
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.insumo.models import Insumo
from apps.medida.models import Medida
from web_project.forms import DecimalBRField

from .models import DIMENSOES_DE_SAIDA, LIMITE_OBSERVACOES, LIMITE_RECEITA, Processado, ProcessadoItem

CONTROLE = {"class": "form-control"}
SELECT = {"class": "form-select"}
CAMPOS_RECEITA = ("receita", "observacoes")


def normalizar_texto_longo(texto):
    """Receita/observações: texto simples. Quebras de linha padronizadas e espaços das pontas
    removidos; HTML nunca é interpretado (as telas e o PDF escapam o conteúdo)."""
    return "\n".join((texto or "").splitlines()).strip()  # splitlines trata CRLF, CR e LF


class CamposReceitaMixin:
    """Campos `receita` e `observacoes` (Processado e Ficha). Só existem no formulário para quem tem a
    permissão `ver_receita` (`ver_receita=True`); sem ela os campos nem são montados, então um POST
    forjado não grava nada e o conteúdo existente é preservado."""

    def configurar_receita(self, ver_receita):
        if not ver_receita:
            for nome in CAMPOS_RECEITA:
                self.fields.pop(nome, None)
            return
        self.fields["receita"].widget = forms.Textarea(attrs={**CONTROLE, "rows": 8, "maxlength": LIMITE_RECEITA,
            "placeholder": "Modo de preparo. Quebras de linha e listas com \"- \" são mantidas."})
        self.fields["observacoes"].widget = forms.Textarea(attrs={**CONTROLE, "rows": 3, "maxlength": LIMITE_OBSERVACOES})

    def clean_receita(self):
        return normalizar_texto_longo(self.cleaned_data.get("receita"))

    def clean_observacoes(self):
        return normalizar_texto_longo(self.cleaned_data.get("observacoes"))


class ProcessadoForm(CamposReceitaMixin, forms.ModelForm):
    """Dados gerais do processado/receita. O custo, o fator e o insumo gerado
    NÃO são campos: são calculados no servidor a cada gravação."""

    rendimento_quantidade = DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), label="Quantidade de rendimento",
        # data-calc: prévia "por extenso" ("100 gramas") ao lado do campo (static/js/calculo-medida.js)
        widget=forms.TextInput(attrs={**CONTROLE, "placeholder": "Ex: 500", "data-mask": "decimal", "data-calc": "quantidade"}),
        error_messages={"invalid": "Informe um rendimento válido.", "min_value": "O rendimento deve ser maior que zero."},
    )

    class Meta:
        model = Processado
        fields = ["nome", "rendimento_quantidade", "rendimento_medida", "receita", "observacoes", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE),
            "rendimento_medida": forms.Select(attrs={**SELECT, "data-calc": "medida"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, ver_receita=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_receita(ver_receita)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Saída só em massa ou volume (g, kg, mL, L); ao editar, a atual continua válida.
        filtro = Q(ativo=True, dimensao__in=DIMENSOES_DE_SAIDA)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.rendimento_medida_id)
        self.fields["rendimento_medida"].queryset = Medida.objects.filter(filtro).order_by("id")
        self.fields["rendimento_medida"].empty_label = "Selecione..."
        self.fields["rendimento_medida"].label_from_instance = rotulo_da_medida

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome é obrigatório.")
        repetido = Processado.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.pk)
        # O processado vira um insumo com o mesmo nome: não pode colidir com um insumo existente.
        insumos = Insumo.objects.filter(cliente=self.cliente, nome__iexact=nome).exclude(pk=self.instance.insumo_id)
        if repetido.exists() or insumos.exists():
            raise forms.ValidationError("Já existe um insumo ou processado com este nome.")
        return nome


def sigla_de_exibicao(medida):
    """"kg" -> "Kg", "L" -> "L", "un" -> "Un": a sigla como aparece nos seletores de insumo."""
    return medida.sigla[:1].upper() + medida.sigla[1:]


def rotulo_da_medida(medida):
    """Nome completo da unidade nos dropdowns: "Quilo (kg)", "Gramas (g)". O valor enviado (id) não muda."""
    return f"{medida.medida} ({medida.sigla})"


def maiores_grandezas():
    """{dimensão: Medida de maior grandeza} (Quilo, Litro, Unidade, Metro...), em 1 consulta."""
    from .recalculo import UNIDADE_PADRAO_PRECO  # import tardio: evita ciclo entre módulos

    fatores = {dim: fator for dim, (fator, _sigla) in UNIDADE_PADRAO_PRECO.items()}
    maiores = {}
    for medida in Medida.objects.filter(ativo=True).order_by("id"):
        if fatores.get(medida.dimensao) == medida.fator_base:
            maiores.setdefault(medida.dimensao, medida)
    return maiores


class ProcessadoItemForm(forms.ModelForm):
    quantidade = DecimalBRField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), label="Quantidade usada",
        widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
        error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "A quantidade deve ser maior que zero."},
    )

    class Meta:
        model = ProcessadoItem
        fields = ["insumo", "medida", "quantidade"]
        widgets = {
            "insumo": forms.Select(attrs=SELECT),
            "medida": forms.Select(attrs=SELECT),
        }

    def __init__(self, *args, insumos=None, maiores=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Só insumos da MESMA unidade (tenant); o campo rejeita ids de fora.
        self.fields["insumo"].queryset = insumos if insumos is not None else Insumo.objects.none()
        self.fields["insumo"].empty_label = "Selecione..."
        maiores = maiores if maiores is not None else maiores_grandezas()
        self.fields["insumo"].label_from_instance = lambda i: self._rotulo_do_insumo(i, maiores)
        # Todas as unidades ativas, com o nome completo. A primeira opção ("Unidade...") fica selecionada: quem escolhe
        # a unidade é o usuário (o servidor converte para a unidade do insumo).
        self.fields["medida"].queryset = Medida.objects.filter(ativo=True).order_by("id")
        self.fields["medida"].empty_label = "Unidade..."
        self.fields["medida"].label_from_instance = rotulo_da_medida

    @staticmethod
    def _rotulo_do_insumo(insumo, maiores):
        """"Açúcar (Kg)": nome + a maior grandeza da dimensão do insumo."""
        maior = maiores.get(insumo.medida.dimensao)
        return f"{insumo.nome} ({sigla_de_exibicao(maior)})" if maior else insumo.nome


class BaseItemFormSet(BaseInlineFormSet):
    # Segmentos Serviço e Produto e Serviço: insumos deixam de ser obrigatórios na ficha (a ficha pode ter só mão de obra, ou nada).
    itens_opcionais = False

    def __init__(self, *args, cliente=None, itens_opcionais=False, **kwargs):
        self.itens_opcionais = itens_opcionais
        # Insumos selecionáveis: ativos da unidade, exceto o gerado por este
        # próprio processado (evita ciclo direto; o indireto é checado ao salvar).
        self.insumos = Insumo.objects.filter(cliente=cliente, ativo=True).select_related("medida").order_by("nome")
        proprio = getattr(kwargs.get("instance"), "insumo_id", None)
        if proprio:
            self.insumos = self.insumos.exclude(pk=proprio)
        self.maiores = maiores_grandezas()  # 1 consulta para todas as linhas do formset
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["insumos"] = self.insumos
        kwargs["maiores"] = self.maiores
        return kwargs

    def itens_validos(self):
        return [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        itens = self.itens_validos()
        if not itens and not self.itens_opcionais:
            raise forms.ValidationError("Inclua ao menos um insumo na ficha técnica.")
        ids = [f.cleaned_data["insumo"].pk for f in itens]
        if len(ids) != len(set(ids)):
            raise forms.ValidationError("Um insumo só pode aparecer uma vez na ficha técnica; some as quantidades.")


ItemFormSet = inlineformset_factory(
    Processado, ProcessadoItem, form=ProcessadoItemForm, formset=BaseItemFormSet, extra=1, can_delete=True,
)
