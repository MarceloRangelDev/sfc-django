"""Serializers dos recursos operacionais da API v1: custos comuns, movimentações de estoque e a
distribuição do custo operacional por categoria.

Como no restante da API, as regras vivem nos serviços das telas (apps/estoque/services.py,
apps/custo/services.py); aqui só se descreve o formato. Dinheiro é sempre inteiro em CENTAVOS e
`cliente` nunca é aceito na entrada (vem do usuário autenticado)."""
from decimal import Decimal

from django.db.models import Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.custo.models import CustoModelo
from apps.estoque.models import MOTIVO_CHOICES, MovimentoEstoque
from apps.medida.models import Medida
from web_project.regras.dinheiro import DinheiroService


def _erro_unico(mensagem):
    return serializers.ValidationError(mensagem, code="unique")  # a API responde 409


class CustoComumSerializer(serializers.ModelSerializer):
    origem = serializers.SerializerMethodField(help_text='"sistema" (padrão, somente leitura) ou "proprio" (da empresa do usuário).')

    class Meta:
        model = CustoModelo
        fields = ["id", "nome", "tipo", "descricao", "ativo", "origem", "criado_em", "atualizado_em"]
        read_only_fields = ["criado_em", "atualizado_em"]
        validators = []  # unicidade tratada em validate_nome (padrões do sistema + próprios)

    @extend_schema_field(serializers.CharField())
    def get_origem(self, comum):
        return "sistema" if comum.padrao_do_sistema else "proprio"

    def validate_nome(self, nome):
        nome = " ".join(nome.split())
        if not nome:
            raise serializers.ValidationError("Informe o nome do custo comum.")
        cliente = getattr(getattr(self.context["request"].user, "profile", None), "cliente", None)
        repetido = CustoModelo.objects.filter(Q(cliente=cliente) | Q(cliente__isnull=True), nome__iexact=nome)
        if self.instance:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise _erro_unico("Já existe um custo comum com este nome.")
        return nome


class MovimentoEstoqueSerializer(serializers.ModelSerializer):
    insumo_nome = serializers.CharField(source="insumo.nome", read_only=True)
    unidade = serializers.CharField(source="insumo.medida.sigla", read_only=True, help_text="Unidade em que a quantidade é guardada (a do insumo).")
    tipo_rotulo = serializers.CharField(source="get_tipo_display", read_only=True)
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=4, read_only=True)
    saldo_lote_apos = serializers.DecimalField(max_digits=15, decimal_places=4, read_only=True)
    lote = serializers.SerializerMethodField(help_text="Código do lote informado; vazio se o movimento não tem lote.")
    validade = serializers.DateField(source="lote.validade", read_only=True, allow_null=True)
    editavel = serializers.BooleanField(read_only=True, help_text="False nos movimentos de transferência.")

    class Meta:
        model = MovimentoEstoque
        fields = [
            "id", "insumo", "insumo_nome", "tipo", "tipo_rotulo", "quantidade", "unidade", "lote", "validade",
            "saldo_lote_apos", "responsavel_nome", "transferencia", "motivo", "observacao", "data", "editado_em", "editavel",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_lote(self, movimento):
        return movimento.lote.codigo_exibicao


class MovimentoEstoqueCriacaoSerializer(serializers.Serializer):
    """Corpo de POST /estoque/movimentos/ (entrada, saída ou ajuste)."""

    insumo = serializers.IntegerField(help_text="Id de um insumo ATIVO da sua unidade (ver /insumos/).")
    tipo = serializers.ChoiceField(choices=[MovimentoEstoque.ENTRADA, MovimentoEstoque.SAIDA, MovimentoEstoque.AJUSTE_ENTRADA, MovimentoEstoque.AJUSTE_SAIDA])
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), help_text='Com ponto decimal (ex.: "2.5").')
    medida = serializers.IntegerField(required=False, help_text="Unidade da quantidade (padrão: a do insumo; precisa ser da mesma dimensão).")
    lote = serializers.CharField(max_length=60, required=False, allow_blank=True, help_text="Só em entradas; opcional.")
    validade = serializers.DateField(required=False, allow_null=True, help_text="Só em entradas; pode ser informada sem lote.")
    motivo = serializers.ChoiceField(
        choices=[m for m, _ in MOTIVO_CHOICES], required=False, allow_blank=True,
        help_text='OBRIGATÓRIO nas baixas manuais (tipo "saida" e "ajuste_saida"); ignorado nas entradas. "outro" exige `observacao`.',
    )
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


class MovimentoEstoqueEdicaoSerializer(serializers.Serializer):
    """Corpo de PUT/PATCH /estoque/movimentos/{id}/. Insumo, tipo e lote não podem ser alterados."""

    quantidade = serializers.DecimalField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), required=False,
        help_text='Nova quantidade, com ponto decimal (ex.: "2.5"), na unidade informada em `medida`.',
    )
    medida = serializers.PrimaryKeyRelatedField(
        queryset=Medida.objects.filter(ativo=True), required=False,
        help_text="Id da unidade em que a quantidade foi digitada (padrão: a unidade do insumo).",
    )
    data = serializers.DateTimeField(required=False, help_text="Data e hora da movimentação (ISO 8601); não pode estar no futuro.")
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    validade = serializers.DateField(
        required=False, allow_null=True,
        help_text="Só para entradas e ajustes de acréscimo. Omitido = mantém; null = remove. Só muda se o lote tem uma única movimentação.",
    )


class CustoCategoriaSerializer(serializers.Serializer):
    """Uma categoria na distribuição do custo operacional."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    ativa = serializers.BooleanField()
    quantidade_vendida = serializers.IntegerField(help_text="Vendas do mês informadas na categoria (base do rateio).")
    valor = serializers.IntegerField(help_text="Custo operacional da categoria no mês, em centavos.")
    valor_formatado = serializers.CharField()
    por_unidade = serializers.IntegerField(help_text="Custo por item vendido, em centavos (0 sem vendas).")
    origem = serializers.ChoiceField(choices=["vendas", "igual", "manual", "zero"], help_text="Como o valor foi obtido.")
    ajuste_manual = serializers.BooleanField()


class CustoDistribuicaoSerializer(serializers.Serializer):
    """Resposta de GET /custo-operacional/categorias/: totais (centavos) e a lista de categorias."""

    total = serializers.IntegerField(help_text="Custo operacional total do mês (soma dos custos ativos), em centavos.")
    distribuido = serializers.IntegerField(help_text="Parte do total que cabe às categorias. Fecha com o total, salvo categorias ajustadas sem categoria para absorver a diferença.")
    nao_distribuido = serializers.IntegerField(help_text="Centavos sem categoria para receber (todas ajustadas ou nenhuma ativa).")
    excedente_manual = serializers.IntegerField(help_text="Quanto os ajustes manuais passam do total (0 se não passam).")
    sem_historico = serializers.BooleanField(help_text="True quando nenhuma categoria tem vendas e o rateio foi dividido igualmente.")
    categorias = CustoCategoriaSerializer(many=True)


class CustoCategoriaAjusteSerializer(serializers.Serializer):
    valor = serializers.IntegerField(min_value=0, help_text="Valor fixado para a categoria, em centavos (R$ 300,00 = 30000).")


def formatar(centavos):
    return DinheiroService.formatar(centavos)
