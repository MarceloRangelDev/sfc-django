"""API do estoque de PRODUTOS (saldo próprio): saldos e movimentos. Mesmas permissões do estoque de insumos
(`estoque.*_movimentoestoque`) e sempre limitado à empresa do usuário (`cliente_do_usuario`): o cliente nunca vem do corpo."""
from decimal import Decimal

from django.db.models import Q
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import mixins, serializers, viewsets

from apps.estoque.forms import MovimentoProdutoForm
from apps.estoque.models import MOTIVO_CHOICES, MovimentoProduto
from apps.estoque.produto_estoque import (
    TIPOS_MANUAIS_DE_ENTRADA, produtos_com_saldo, registrar_baixa_manual_produto, registrar_entrada_produto,
)
from apps.produto.models import Produto
from web_project.acesso import cliente_do_usuario

from .negocio import erros_de
from .permissions import PermissaoDoSistema
from .views import PARAMETROS_BUSCA, _ordenar, resposta


class SaldoProdutoSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Id do produto.")
    nome = serializers.CharField()
    categoria = serializers.IntegerField(source="categoria_id")
    saldo = serializers.DecimalField(max_digits=15, decimal_places=3, help_text="Saldo atual (nunca negativo).")
    situacao = serializers.SerializerMethodField(help_text='"zerado" ou "ok".')

    @extend_schema_field(serializers.CharField())
    def get_situacao(self, produto):
        return "zerado" if produto.saldo == 0 else "ok"


class MovimentoProdutoSerializer(serializers.ModelSerializer):
    produto_nome = serializers.CharField(source="produto.nome", read_only=True)
    tipo_rotulo = serializers.CharField(source="get_tipo_display", read_only=True)
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=3, read_only=True)
    saldo_apos = serializers.DecimalField(max_digits=15, decimal_places=3, read_only=True)
    venda = serializers.SerializerMethodField(help_text="Id da venda do PDV (baixa/estorno automático); nulo nos lançamentos manuais.")

    class Meta:
        model = MovimentoProduto
        fields = ["id", "produto", "produto_nome", "tipo", "tipo_rotulo", "quantidade", "saldo_apos", "motivo", "observacao", "responsavel_nome", "venda", "data"]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_venda(self, movimento):
        return movimento.venda_item.venda_id if movimento.venda_item_id else None


class MovimentoProdutoCriacaoSerializer(serializers.Serializer):
    """Corpo de POST /estoque/produtos/movimentos/ (entrada, baixa manual ou ajuste)."""

    produto = serializers.IntegerField(help_text="Id de um produto ATIVO da sua empresa que controla estoque (ver /produtos/).")
    tipo = serializers.ChoiceField(choices=[MovimentoProduto.ENTRADA, MovimentoProduto.SAIDA, MovimentoProduto.AJUSTE_ENTRADA, MovimentoProduto.AJUSTE_SAIDA])
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=3, min_value=Decimal("0.001"), help_text='Com ponto decimal (ex.: "2.5").')
    motivo = serializers.ChoiceField(
        choices=[m for m, _ in MOTIVO_CHOICES], required=False, allow_blank=True,
        help_text='OBRIGATÓRIO nas baixas manuais ("saida" e "ajuste_saida"); ignorado nas entradas. "outro" exige `observacao`.',
    )
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


@extend_schema(tags=["estoque"])
class EstoqueProdutoSaldoViewSet(viewsets.GenericViewSet):
    """Saldos dos produtos ativos da empresa que controlam estoque (`controla_estoque` em /produtos/). Somente leitura."""

    serializer_class = SaldoProdutoSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "movimentoestoque", "estoque"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Produto.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        if cliente is None:
            return Produto.objects.none()
        queryset = produtos_com_saldo([cliente])
        params = self.request.query_params
        if self.action == "list":
            if (params.get("q") or "").strip():
                queryset = queryset.filter(nome__icontains=params["q"].strip())
            if params.get("situacao") == "zerado":
                queryset = queryset.filter(saldo=0)
        return _ordenar(queryset, params, {"nome": "nome", "saldo": "saldo"}, ["nome"])

    @extend_schema(parameters=[*PARAMETROS_BUSCA, OpenApiParameter("situacao", str, enum=["zerado"]), OpenApiParameter("ordenar", str, description="nome, saldo (prefixo - = decrescente)")],
                   responses={200: SaldoProdutoSerializer(many=True)})
    def list(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(SaldoProdutoSerializer(pagina, many=True).data)

    @extend_schema(responses={200: SaldoProdutoSerializer})
    def retrieve(self, request, pk=None):
        return resposta(SaldoProdutoSerializer(self.get_object()).data)


@extend_schema(tags=["estoque"])
class MovimentoProdutoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Histórico de movimentos do estoque dos produtos da empresa. POST registra entrada, BAIXA MANUAL ou ajuste. O histórico é
    imutável (sem edição/exclusão): erros se corrigem com um ajuste. A baixa/estorno por venda do PDV é automática e idempotente."""

    serializer_class = MovimentoProdutoSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "movimentoestoque", "estoque"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MovimentoProduto.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        queryset = MovimentoProduto.objects.select_related("produto", "venda_item").filter(cliente=cliente) if cliente else MovimentoProduto.objects.none()
        params = self.request.query_params
        if self.action == "list":
            if params.get("produto", "").isdigit():
                queryset = queryset.filter(produto_id=params["produto"])
            if params.get("tipo") in dict(MovimentoProduto.TIPO_CHOICES):
                queryset = queryset.filter(tipo=params["tipo"])
            de, ate = parse_date(params.get("data_de", "")), parse_date(params.get("data_ate", ""))
            if de:
                queryset = queryset.filter(data__date__gte=de)
            if ate:
                queryset = queryset.filter(data__date__lte=ate)
            if (params.get("q") or "").strip():
                termo = params["q"].strip()
                queryset = queryset.filter(Q(produto__nome__icontains=termo) | Q(responsavel_nome__icontains=termo) | Q(observacao__icontains=termo))
        return _ordenar(queryset, params, {"data": "data", "quantidade": "quantidade", "produto": "produto__nome"}, ["-data", "-pk"])

    @extend_schema(parameters=[
        OpenApiParameter("produto", int), OpenApiParameter("tipo", str, enum=[t for t, _ in MovimentoProduto.TIPO_CHOICES]),
        OpenApiParameter("data_de", str, description="AAAA-MM-DD"), OpenApiParameter("data_ate", str, description="AAAA-MM-DD"),
        *PARAMETROS_BUSCA, OpenApiParameter("ordenar", str, description="data, quantidade, produto (prefixo - = decrescente; padrão: mais recentes)"),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=MovimentoProdutoCriacaoSerializer, responses={201: MovimentoProdutoSerializer},
        examples=[
            OpenApiExample("Entrada", value={"produto": 1, "tipo": "entrada", "quantidade": "24"}, request_only=True),
            OpenApiExample("Baixa manual por perda", value={"produto": 1, "tipo": "saida", "quantidade": "2", "motivo": "perda", "observacao": "caiu no chão"}, request_only=True),
        ],
        description="Registra entrada, baixa manual ou ajuste na empresa do usuário. Baixas exigem `motivo`. "
                    "Erros: 400 (campos/motivo/produto de outra empresa ou sem controle de estoque), 422 (saldo insuficiente).",
    )
    def create(self, request, *args, **kwargs):
        entrada = MovimentoProdutoCriacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        # O formulário da tela valida tudo (produto da empresa e com controle, motivo, observação) — mesma regra nas duas portas.
        form = MovimentoProdutoForm(
            {"produto": dados["produto"], "tipo": dados["tipo"], "quantidade": str(dados["quantidade"]), "motivo": dados.get("motivo", ""),
             "observacao": dados.get("observacao", "")},
            cliente=cliente_do_usuario(request.user),
        )
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        d = form.cleaned_data
        if d["tipo"] in TIPOS_MANUAIS_DE_ENTRADA:
            movimento = registrar_entrada_produto(request.user, d["produto"], d["quantidade"], d["observacao"], tipo=d["tipo"])
        else:
            movimento = registrar_baixa_manual_produto(request.user, d["produto"], d["quantidade"], d["motivo"], d["observacao"], tipo=d["tipo"])
        movimento = MovimentoProduto.objects.select_related("produto", "venda_item").get(pk=movimento.pk)
        return resposta(MovimentoProdutoSerializer(movimento).data, "Movimentação registrada.", 201)
