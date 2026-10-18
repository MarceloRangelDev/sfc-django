from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.medida.models import Medida
from apps.estoque.services import insumo_tem_estoque
from apps.processado.services import mensagem_insumo_em_uso
from web_project.forms import DecimalBRField, MoedaCentavosField, UnidadeFormMixin
from web_project.regras import RegraNegocioError

from apps.insumoglobal import services as globais

from .models import LIMITE_CENTAVOS, Insumo


class InsumoForm(UnidadeFormMixin, forms.ModelForm):
    """Cadastro/edição de insumo.

    O usuário informa: nome, unidade comprada, quantidade comprada e valor
    pago. O preço por KG/L/UN NÃO é um campo do formulário: é calculado no
    backend (Insumo.save) a partir desses três dados — a tela só mostra uma
    prévia (via /medidas/calcular), nunca decide o valor.
    """

    quantidade = DecimalBRField(
        max_digits=15,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        label="Quantidade comprada",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 100", "data-mask": "decimal", "data-calc": "quantidade"}),
        error_messages={
            "invalid": "Informe uma quantidade válida.",
            "min_value": "A quantidade deve ser maior que zero.",
        },
    )
    preco_pago = MoedaCentavosField(
        label="Valor pago (R$)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: 4,00", "data-mask": "moeda", "data-calc": "preco"}),
        error_messages={"invalid": "Informe um valor válido.", "min_value": "O valor não pode ser negativo."},
    )

    # Só aparece (no template) quando o nome digitado lembra um padrão existente: o usuário confirma que é mesmo um insumo novo.
    confirmar_novo = forms.BooleanField(required=False, label="Cadastrar mesmo assim (é um insumo novo)")

    class Meta:
        model = Insumo
        fields = ["nome", "marca", "medida", "quantidade", "preco_pago", "dias_validade", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control", "list": "insumosGlobais", "autocomplete": "off"}),
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Opcional", "maxlength": 60, "list": "marcasGlobais", "autocomplete": "off"}),
            "medida": forms.Select(attrs={"class": "form-select", "data-calc": "medida"}),
            "dias_validade": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 32000, "placeholder": "Opcional (ex.: 5)"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        # Só medidas ativas aparecem para novos cadastros; ao editar, a
        # medida atual do insumo continua selecionável mesmo se inativada.
        filtro = Q(ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.medida_id)
        self.fields["medida"].queryset = Medida.objects.filter(filtro).order_by("id")
        self.fields["medida"].empty_label = "Selecione..."

    def clean_nome(self):
        nome = (self.cleaned_data.get("nome") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome do insumo é obrigatório.")
        queryset = Insumo.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Este insumo já está cadastrado.")
        return self._padronizar_nome(nome)

    @property
    def nomes_globais(self):
        """Nomes padrão para o autocompletar do campo (<datalist>)."""
        return [g.nome for g in globais._ativos(self._segmento(), todos=False)]

    @property
    def marcas_globais(self):
        """Marcas padrão para o autocompletar do campo Marca."""
        return list(globais.MarcaGlobal.objects.filter(ativo=True).values_list("nome", flat=True))

    def _segmento(self):
        cliente = self.cliente_alvo() if hasattr(self, "cleaned_data") else self.cliente
        return getattr(cliente, "segmento", None)

    def _padronizar_nome(self, nome):
        """Cadastro global: nome já padronizado -> usa o nome padrão; inexistente -> só segue se não lembrar um padrão (ou se o usuário
        confirmar), e o insumo fica FORA DO PADRÃO até o administrador padronizar. Edição sem trocar o nome não mexe em nada."""
        self._padrao, self._decidiu_padrao, self.similares = None, False, []
        if self.instance.pk and nome == self.instance.nome:
            return nome
        self._decidiu_padrao = True
        padrao = globais.achar_exato(nome, self._segmento(), todos=False)
        if padrao is not None:
            self._padrao = padrao
            outro = Insumo.objects.filter(cliente=self.cliente_alvo(), nome__iexact=padrao.nome)
            if self.instance.pk:
                outro = outro.exclude(pk=self.instance.pk)
            if outro.exists():
                raise forms.ValidationError(f"“{padrao.nome}” já está cadastrado.")
            return padrao.nome
        self.similares = globais.semelhantes(nome, segmento=self._segmento(), todos=False)
        if self.similares and not self.data.get("confirmar_novo"):
            nomes = ", ".join(f"“{g.nome}”" for g in self.similares)
            raise forms.ValidationError(f"Existe no cadastro padrão: {nomes}. Use o nome padronizado ou marque “Cadastrar mesmo assim”.")
        return nome

    def save(self, commit=True):
        insumo = super().save(commit=False)
        if getattr(self, "_decidiu_padrao", False):
            insumo.insumo_global, insumo.nao_conforme = self._padrao, self._padrao is None
        # Marca padronizada: se o insumo é padrão e a marca digitada é uma variação dele, usa o nome padrão da marca e a foto dela.
        variacao = globais.variacao_para(insumo.insumo_global, insumo.marca)
        insumo.insumo_global_marca = variacao
        if variacao is not None:
            insumo.marca = variacao.marca.nome
        if commit:
            insumo.save()
            self.save_m2m()
            if insumo.nao_conforme and getattr(self, "_decidiu_padrao", False):
                globais.avisar_novo_fora_do_padrao(insumo)
        return insumo

    def clean_marca(self):
        # Opcional: espaços nas pontas e repetidos são normalizados; vazio continua válido.
        return " ".join((self.cleaned_data.get("marca") or "").split())

    def clean_preco_pago(self):
        valor = self.cleaned_data.get("preco_pago")
        if valor is not None and valor > LIMITE_CENTAVOS:
            raise forms.ValidationError("Valor acima do limite permitido.")
        return valor

    def clean(self):
        dados = super().clean()
        medida_nova = dados.get("medida")
        if self.instance.pk and medida_nova and medida_nova.pk != self.instance.medida_id and insumo_tem_estoque(self.instance):
            # O saldo do estoque está guardado na unidade atual: trocá-la mudaria o significado dele.
            self.add_error("medida", "Este insumo tem movimentações de estoque; a unidade de medida não pode ser alterada.")
        if self.instance.pk and medida_nova and medida_nova.dimensao != self.instance.medida.dimensao:
            # As receitas que usam este insumo guardam unidades da dimensão antiga;
            # trocar (ex.: kg -> L) tornaria o custo delas incalculável.
            erro = mensagem_insumo_em_uso(self.instance)
            if erro:
                self.add_error("medida", "Este insumo é usado em receitas; a unidade deve continuar na mesma dimensão.")
        # Antecipa o erro de cálculo (ex.: preço por unidade gigante) com
        # mensagem amigável em vez de falhar só ao gravar.
        if all(dados.get(campo) is not None for campo in ("medida", "quantidade", "preco_pago")):
            tentativa = Insumo(medida=dados["medida"], quantidade=dados["quantidade"], preco_pago=dados["preco_pago"])
            try:
                tentativa.calcular_preco_unidade()
            except RegraNegocioError as erro:
                raise forms.ValidationError(str(erro)) from None
        return dados
