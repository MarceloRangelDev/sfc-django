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

    # Insumo do cadastro global (padronizado). Vazio = "Personalizar": o usuário informa o nome e o insumo vai para a fila "Fora do padrão".
    insumo_padrao = forms.ModelChoiceField(
        queryset=globais.InsumoGlobal.objects.none(), required=False, empty_label="Personalizar (não está na lista)", label="Insumo",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Insumo
        fields = ["nome", "marca", "medida", "quantidade", "preco_pago", "dias_validade", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": "form-control", "list": "insumosGlobais", "autocomplete": "off"}),
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Opcional", "maxlength": 60, "autocomplete": "off"}),
            "medida": forms.Select(attrs={"class": "form-select", "data-calc": "medida"}),
            "dias_validade": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 32000, "placeholder": "Opcional (ex.: 5)"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_unidade(cliente, unidades)
        self._era_nao_conforme = bool(self.instance.pk and self.instance.nao_conforme)
        self._padrao, self._decidiu_padrao, self._variacao, self._marca_personalizada = None, False, None, False
        self.fields["nome"].required = False  # obrigatório só no modo "Personalizar" (validado em _definir_padrao)
        visiveis = globais.visiveis_no_segmento(globais.InsumoGlobal.objects.filter(ativo=True), getattr(self.cliente, "segmento", None))
        if self.instance.pk and self.instance.insumo_global_id:
            visiveis = globais.InsumoGlobal.objects.filter(Q(pk__in=visiveis.values("pk")) | Q(pk=self.instance.insumo_global_id))  # o atual continua selecionável
        self.fields["insumo_padrao"].queryset = visiveis.order_by("nome")
        if self.instance.pk and self.instance.insumo_global_id:
            self.fields["insumo_padrao"].initial = self.instance.insumo_global_id
        # Só medidas ativas aparecem para novos cadastros; ao editar, a
        # medida atual do insumo continua selecionável mesmo se inativada.
        filtro = Q(ativo=True)
        if self.instance.pk:
            filtro |= Q(pk=self.instance.medida_id)
        self.fields["medida"].queryset = Medida.objects.filter(filtro).order_by("id")
        self.fields["medida"].empty_label = "Selecione..."

    @property
    def info_padrao(self):
        """Texto de ajuda sob a lista: segmento do cliente e quantos insumos globais ele enxerga (explica a lista vazia)."""
        segmento = getattr(self.cliente, "segmento", None)
        total = self.fields["insumo_padrao"].queryset.count()
        if total:
            return ""
        return (f"Nenhum insumo global liberado para o segmento “{segmento.nome}”. " if segmento else "Este cliente não tem segmento definido. ") +             "Peça ao administrador para marcar o segmento nos Insumos globais (ou em Segmentos)."

    @property
    def marcas_por_padrao(self):
        """{id do insumo padrão: [{id, nome}]} das marcas (fabricantes) ativas, para o dropdown de Marca (a foto vem da marca escolhida)."""
        mapa = {}
        for v in globais.InsumoGlobalMarca.objects.filter(ativo=True, marca__ativo=True, insumo_global__in=self.fields["insumo_padrao"].queryset).select_related("marca"):
            mapa.setdefault(str(v.insumo_global_id), []).append({"id": v.pk, "nome": v.marca.nome})
        return mapa

    @property
    def marca_opcao(self):
        """Valor do dropdown de marca ao exibir o formulário: id da variação, "personalizar" ou "" (sem marca)."""
        if self.is_bound:
            return self.data.get("marca_padrao", "")
        if self.instance.pk and self.instance.insumo_global_marca_id:
            return str(self.instance.insumo_global_marca_id)
        return "personalizar" if self.instance.pk and self.instance.marca else ""

    def _segmento(self):
        cliente = self.cliente_alvo() if hasattr(self, "cleaned_data") else self.cliente
        return getattr(cliente, "segmento", None)

    def _definir_padrao(self, dados):
        """Cadastro global. Escolheu um insumo da lista -> usa o nome e o vínculo dele. "Personalizar" (nada escolhido) -> usa o nome digitado
        (se coincidir com um padrão do segmento, vincula a ele); senão o insumo fica FORA DO PADRÃO até o administrador padronizar.
        Edição que não troca o insumo/nome não mexe no vínculo."""
        self._padrao, self._decidiu_padrao = None, False
        padrao = dados.get("insumo_padrao")
        nome = " ".join((dados.get("nome") or "").split())
        if padrao is not None:
            nome = padrao.nome
        elif not nome:
            self.add_error("nome", "Escolha um insumo da lista ou use “Personalizar” e informe o nome.")
            return
        else:
            padrao = globais.achar_exato(nome, self._segmento(), todos=False)
            if padrao is not None:
                nome = padrao.nome
        repetido = Insumo.objects.filter(cliente=self.cliente_alvo(), nome__iexact=nome)
        if self.instance.pk:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            self.add_error("insumo_padrao" if dados.get("insumo_padrao") is not None else "nome", "Este insumo já está cadastrado.")
            return
        dados["nome"] = nome
        mudou = not self.instance.pk or nome != self.instance.nome or (padrao.pk if padrao else None) != self.instance.insumo_global_id
        if mudou:
            self._decidiu_padrao, self._padrao = True, padrao

    def _definir_marca(self, dados):
        """Marca (fabricante). Com insumo padrão: escolhe uma marca do dropdown (vira a variação, com a foto dela) ou "Personalizar" digitando
        outra; marca personalizada deixa o insumo FORA DO PADRÃO. Sem insumo padrão (personalizado): marca é texto livre."""
        padrao = self._padrao if self._decidiu_padrao else self.instance.insumo_global
        texto = " ".join((dados.get("marca") or "").split())
        opcao = (self.data.get("marca_padrao") or "").strip()
        self._variacao, self._marca_personalizada = None, False
        if padrao is None:
            dados["marca"] = texto
            return
        if opcao.isdigit():
            variacao = globais.InsumoGlobalMarca.objects.filter(pk=opcao, insumo_global=padrao, ativo=True, marca__ativo=True).select_related("marca").first()
            if variacao is None:
                self.add_error("marca", "Marca inválida para este insumo. Escolha uma da lista ou use “Personalizar”.")
                return
            self._variacao, dados["marca"] = variacao, variacao.marca.nome
        elif opcao == "personalizar" or (not opcao and texto):
            if not texto:
                self.add_error("marca", "Informe o nome da marca personalizada.")
                return
            variacao = globais.variacao_para(padrao, texto)  # digitou uma marca que já existe para o insumo: usa a padronizada
            if variacao is not None:
                self._variacao, dados["marca"] = variacao, variacao.marca.nome
            else:
                self._marca_personalizada, dados["marca"] = True, texto
        else:
            dados["marca"] = ""

    def save(self, commit=True):
        insumo = super().save(commit=False)
        if getattr(self, "_decidiu_padrao", False):
            insumo.insumo_global = self._padrao
        insumo.insumo_global_marca = self._variacao
        # Fora do padrão: insumo personalizado (sem padrão) OU marca personalizada (padrão escolhido, marca que não está na lista da marca).
        insumo.nao_conforme = insumo.insumo_global is None or self._marca_personalizada
        if commit:
            insumo.save()
            self.save_m2m()
            if insumo.nao_conforme and not self._era_nao_conforme:
                globais.avisar_novo_fora_do_padrao(insumo)  # só avisa quando passa a ser fora do padrão (não a cada edição)
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
        self._definir_padrao(dados)
        self._definir_marca(dados)
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
