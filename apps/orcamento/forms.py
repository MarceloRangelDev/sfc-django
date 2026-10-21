"""Formulários do orçamento. Só coletam e convertem campos; a validação de verdade (produto da unidade, preço, descontos, totais) é do
`services.validar_linhas`/`calcular_totais`, a mesma usada pela API."""
from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from apps.consumidor.models import Consumidor
from apps.produto.models import Produto
from web_project.forms import DecimalBRField, MoedaCentavosField

from .models import Orcamento, OrcamentoItem

CONTROLE = {"class": "form-control"}


class OrcamentoForm(forms.ModelForm):
    desconto = MoedaCentavosField(required=False, label="Desconto geral (R$)", widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda"}))

    class Meta:
        model = Orcamento
        fields = ["consumidor", "destinatario_nome", "destinatario_email", "destinatario_whatsapp", "validade", "observacoes", "desconto"]
        widgets = {
            "consumidor": forms.Select(attrs={"class": "form-select"}),
            "destinatario_nome": forms.TextInput(attrs=CONTROLE),
            "destinatario_email": forms.EmailInput(attrs=CONTROLE),
            "destinatario_whatsapp": forms.TextInput(attrs={**CONTROLE, "placeholder": "+5511999990000", "inputmode": "tel"}),
            "validade": forms.DateInput(attrs={**CONTROLE, "type": "date"}, format="%Y-%m-%d"),
            "observacoes": forms.Textarea(attrs={**CONTROLE, "rows": 3, "maxlength": 1000}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente
        # Só consumidores ATIVOS e não anonimizados da MESMA unidade (o campo rejeita ids de fora); o atual segue válido ao editar.
        from django.db.models import Q

        filtro = Q(cliente=self.cliente, ativo=True, anonimizado_em__isnull=True)
        if self.instance.pk and self.instance.consumidor_id:
            filtro |= Q(pk=self.instance.consumidor_id)
        self.fields["consumidor"].queryset = Consumidor.objects.filter(filtro).order_by("nome")
        self.fields["consumidor"].required = False
        self.fields["consumidor"].empty_label = "— Digitar os dados abaixo —"
        self.fields["destinatario_nome"].required = False  # vem do consumidor escolhido, se vazio (validado em clean)
        self.fields["validade"].input_formats = ["%Y-%m-%d"]

    def clean_desconto(self):
        return self.cleaned_data.get("desconto") or 0

    def clean(self):
        dados = super().clean()
        consumidor = dados.get("consumidor")
        if consumidor is not None:
            # Copia do cadastro o que não foi digitado (o orçamento guarda o próprio registro; o cadastro pode mudar depois).
            dados["destinatario_nome"] = (dados.get("destinatario_nome") or consumidor.nome).strip()
            dados["destinatario_email"] = dados.get("destinatario_email") or consumidor.email
            dados["destinatario_whatsapp"] = dados.get("destinatario_whatsapp") or consumidor.whatsapp or consumidor.telefone
        if not (dados.get("destinatario_nome") or "").strip():
            self.add_error("destinatario_nome", "Informe o destinatário ou escolha um cliente final.")
        return dados


class LinhaForm(forms.Form):
    """Uma linha de item. `produto` vazio = linha avulsa (descrição e tipo digitados)."""

    produto = forms.ModelChoiceField(queryset=Produto.objects.none(), required=False, widget=forms.Select(attrs={"class": "form-select"}))
    tipo = forms.ChoiceField(choices=OrcamentoItem.TIPO_CHOICES, required=False, widget=forms.Select(attrs={"class": "form-select"}))
    descricao = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=CONTROLE))
    quantidade = DecimalBRField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"), widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "decimal"}),
                                error_messages={"invalid": "Informe uma quantidade válida.", "min_value": "A quantidade deve ser maior que zero."})
    preco_unitario = MoedaCentavosField(required=False, widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda"}))
    desconto = MoedaCentavosField(required=False, widget=forms.TextInput(attrs={**CONTROLE, "data-mask": "moeda"}))

    def __init__(self, *args, produtos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = produtos if produtos is not None else Produto.objects.none()
        self.fields["produto"].empty_label = "Linha avulsa (digitar descrição)"
        self.fields["produto"].label_from_instance = lambda p: f"{p.nome}" + (" (serviço)" if p.tipo == "servico" else "")


class BaseLinhasFormSet(BaseFormSet):
    def __init__(self, *args, cliente=None, produto_atual_ids=(), **kwargs):
        from django.db.models import Q

        # Produtos/serviços ATIVOS da unidade; os já usados neste orçamento seguem válidos mesmo se forem desativados depois.
        filtro = Q(cliente=cliente, ativo=True) | Q(cliente=cliente, pk__in=list(produto_atual_ids))
        self.produtos = Produto.objects.filter(filtro).order_by("nome")
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["produtos"] = self.produtos
        return kwargs

    def linhas(self):
        """Linhas preenchidas (e não removidas) no formato de `services.validar_linhas`."""
        saida = []
        for form in self.forms:
            dados = form.cleaned_data
            if not dados or dados.get("DELETE"):
                continue
            saida.append({
                "produto": dados["produto"].pk if dados.get("produto") else None, "tipo": dados.get("tipo") or "produto", "descricao": dados.get("descricao", ""),
                "quantidade": dados["quantidade"], "preco_unitario": dados.get("preco_unitario"), "desconto": dados.get("desconto") or 0,
            })
        return saida


LinhasFormSet = formset_factory(LinhaForm, formset=BaseLinhasFormSet, extra=1, can_delete=True)


def linhas_iniciais(orcamento):
    """Itens salvos -> valores iniciais do formset (preço e desconto em centavos; o campo mostra em reais)."""
    return [
        {"produto": i.produto_id, "tipo": i.tipo, "descricao": i.descricao, "quantidade": i.quantidade, "preco_unitario": i.preco_unitario, "desconto": i.desconto}
        for i in orcamento.itens.all()
    ]
