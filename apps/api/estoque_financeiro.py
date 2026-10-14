"""Recursos de estoque (saldos, mínimo, transferências, alertas), financeiro (categorias, contas a pagar e a
receber com baixas e estornos, DRE e balanço) e painel da API v1.

Como no restante da API: validação e gravação reaproveitam formulários e serviços das telas, o cliente (tenant)
vem sempre do usuário autenticado, dinheiro é inteiro em CENTAVOS e as permissões são as nativas por ação."""
from decimal import Decimal

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.cliente.models import Cliente
from apps.dashboard.services import PRESETS, indicadores, periodo
from apps.estoque.forms import LIMITE_QUANTIDADE
from apps.estoque.models import Lote, TransferenciaEstoque
from apps.estoque.etiquetas import EtiquetaParametrosForm, resposta_etiquetas
from apps.estoque.services import (
    alertas_de_estoque_baixo, alertas_de_vencimento, anotar_saldo, definir_minimo, MANTER, dias_alerta_vencimento as dias_estoque, transferir,
)
from apps.estoque.views import _situacao, _unidades_da_transferencia
from apps.financeiro.forms import BaixaForm, CategoriaFinanceiraForm, PeriodoForm, conta_form_para
from apps.financeiro.models import STATUS_ABERTA, STATUS_PAGA, STATUS_PARCIAL, TIPO_CATEGORIA_CHOICES, BaixaBase, CategoriaFinanceira
from apps.financeiro.services import CONFIG, balanco, dre, estornar_baixa, registrar_baixa
from apps.insumo.models import Insumo
from apps.medida.models import Medida
from web_project.acesso import cliente_do_usuario
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .negocio import RecursoViewSet, documentar, erros_de, reais
from .permissions import PermissaoDoSistema
from .views import PARAMETRO_ATIVO, PARAMETROS_BUSCA, AtivarInativarMixin, _ordenar, resposta

QUANTIDADE = dict(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), max_value=LIMITE_QUANTIDADE)


def _cliente_ou_erro(request):
    cliente = cliente_do_usuario(request.user)
    if cliente is None:
        raise serializers.ValidationError("Usuário sem empresa vinculada.")
    return cliente


# ===================================================================================================
# Estoque: saldos, mínimo, alertas e transferências
# ===================================================================================================

class LoteSerializer(serializers.ModelSerializer):
    lote = serializers.CharField(source="codigo_exibicao")
    validade = serializers.DateField(allow_null=True)

    class Meta:
        model = Lote
        fields = ["id", "lote", "validade", "saldo"]
        read_only_fields = fields


class SaldoSerializer(serializers.Serializer):
    """Um insumo ativo com o saldo (soma dos lotes, na unidade do insumo), o mínimo e a situação."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    unidade = serializers.CharField(source="medida.sigla", help_text="Unidade em que o saldo é guardado (a do insumo).")
    saldo = serializers.DecimalField(max_digits=15, decimal_places=4)
    minimo = serializers.DecimalField(max_digits=15, decimal_places=4, help_text="0 = sem alerta.")
    situacao = serializers.ChoiceField(choices=["OK", "Baixo", "Zerado"])
    proximo_vencimento = serializers.DateField(allow_null=True)
    lotes = LoteSerializer(many=True, required=False, help_text="Só no detalhe.")


class MinimoEntrada(serializers.Serializer):
    minimo = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=0, max_value=LIMITE_QUANTIDADE, help_text="Quantidade mínima na unidade do insumo; 0 desliga o alerta.")
    fator_reposicao = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=1, max_value=100, required=False, help_text="Lista de compras: sugestão = mínimo × fator − saldo (padrão 2). Omitido = não altera.")
    multiplo_compra = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), max_value=LIMITE_QUANTIDADE, required=False, allow_null=True, help_text="Embalagem: a sugestão é arredondada para cima a um múltiplo. null = remove; omitido = não altera.")


@extend_schema(tags=["estoque"])
class EstoqueSaldoViewSet(viewsets.GenericViewSet):
    """Saldos de estoque por insumo da empresa do usuário (somente os insumos ativos), com mínimo, situação e próximo vencimento.
    PUT em /{insumo}/minimo/ define a quantidade mínima."""

    serializer_class = SaldoSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "movimentoestoque", "estoque"
    permissoes_por_acao = {"minimo": "change", "etiqueta": "view"}

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Insumo.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        queryset = anotar_saldo(Insumo.objects.filter(cliente=cliente, ativo=True).select_related("medida")) if cliente else Insumo.objects.none()
        params = self.request.query_params
        if self.action == "list":
            termo = (params.get("q") or "").strip()
            if termo:
                queryset = queryset.filter(nome__icontains=termo)
            situacao = params.get("situacao")
            if situacao == "baixo":
                queryset = queryset.filter(minimo__gt=0, saldo__lt=F("minimo"))
            elif situacao == "zerado":
                queryset = queryset.filter(saldo=0)
            elif situacao == "vencendo":
                from datetime import timedelta

                queryset = queryset.filter(proximo_vencimento__lte=timezone.localdate() + timedelta(days=dias_estoque()))
        return _ordenar(queryset, params, {"nome": "nome", "saldo": "saldo", "vencimento": "proximo_vencimento"}, ["nome"])

    def _dados(self, insumo, com_lotes=False):
        insumo.situacao = _situacao(insumo)
        if com_lotes:
            insumo.lotes_lista = list(Lote.objects.filter(insumo=insumo).order_by("validade", "criado_em"))
        dados = SaldoSerializer(insumo).data
        if com_lotes:
            dados["lotes"] = LoteSerializer(insumo.lotes_lista, many=True).data
        else:
            dados.pop("lotes", None)
        return dados

    @extend_schema(parameters=[*PARAMETROS_BUSCA, OpenApiParameter("situacao", str, enum=["baixo", "zerado", "vencendo"]), OpenApiParameter("ordenar", str, description="nome, saldo, vencimento (prefixo - = decrescente)")], responses={200: SaldoSerializer(many=True)})
    def list(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response([self._dados(i) for i in pagina])

    @extend_schema(responses={200: SaldoSerializer})
    def retrieve(self, request, pk=None):
        return resposta(self._dados(self.get_object(), com_lotes=True))

    @extend_schema(
        responses={200: OpenApiTypes.BINARY}, summary="Etiquetas do insumo em PDF (com QR Code)",
        parameters=[OpenApiParameter("formato", str, description="a4 (padrão) ou termica"), OpenApiParameter("copias", int, description="1 a 200 (padrão 1)"),
                    OpenApiParameter("lote", str), OpenApiParameter("emissao", str, description="AAAA-MM-DD (padrão hoje)"),
                    OpenApiParameter("modo_validade", str, description="digitada (padrão, exige `validade`) ou calculada (emissão + dias de validade do insumo)"),
                    OpenApiParameter("validade", str, description="AAAA-MM-DD")],
    )
    @action(detail=True, methods=["get"], url_path="etiqueta")
    def etiqueta(self, request, pk=None):
        insumo = self.get_object()
        dados = {"modo_validade": "digitada", "copias": 1, "formato": "a4", **{k: v for k, v in request.query_params.items()}}
        form = EtiquetaParametrosForm(dados, insumo=insumo)
        if not form.is_valid():
            raise serializers.ValidationError({campo: [str(m) for m in erros] for campo, erros in form.errors.items()})
        return resposta_etiquetas(request._request, insumo, form.cleaned_data)

    @extend_schema(request=MinimoEntrada, responses={200: SaldoSerializer}, summary="Define o estoque mínimo do insumo",
                   parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH, description="Id do insumo.")])
    @action(detail=True, methods=["put"], url_path="minimo")
    def minimo(self, request, pk=None):
        insumo = self.get_object()
        entrada = MinimoEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        definir_minimo(request, insumo, dados["minimo"], dados.get("fator_reposicao", MANTER), dados.get("multiplo_compra", MANTER))  # audita
        return resposta(self._dados(self.get_queryset().get(pk=insumo.pk), com_lotes=True), "Estoque mínimo atualizado.")


@extend_schema(tags=["estoque"])
class EstoqueAlertasViewSet(viewsets.ViewSet):
    """Alertas de estoque baixo e de lotes vencidos ou a vencer (prazo configurável) da empresa do usuário."""

    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "movimentoestoque", "estoque"

    @extend_schema(responses={200: inline_serializer("AlertasDeEstoque", {
        "dias_vencimento": serializers.IntegerField(),
        "baixo": inline_serializer("AlertaBaixo", {"insumo": serializers.IntegerField(), "nome": serializers.CharField(), "saldo": serializers.CharField(), "minimo": serializers.CharField(), "unidade": serializers.CharField()}, many=True),
        "vencimento": inline_serializer("AlertaVencimento", {"insumo": serializers.IntegerField(), "nome": serializers.CharField(), "lote": serializers.CharField(), "validade": serializers.DateField(), "saldo": serializers.CharField(), "unidade": serializers.CharField()}, many=True),
    })})
    def list(self, request):
        cliente = _cliente_ou_erro(request)
        baixo = [{"insumo": i.pk, "nome": i.nome, "saldo": f"{i.saldo:.4f}", "minimo": f"{i.minimo:.4f}", "unidade": i.medida.sigla} for i in alertas_de_estoque_baixo([cliente])]
        vencimento = [
            {"insumo": lote.insumo_id, "nome": lote.insumo.nome, "lote": lote.codigo_exibicao, "validade": lote.validade, "saldo": f"{lote.saldo:.4f}", "unidade": lote.insumo.medida.sigla}
            for lote in alertas_de_vencimento([cliente])
        ]
        return resposta({"dias_vencimento": dias_estoque(), "baixo": baixo, "vencimento": vencimento})


class ItemTransferenciaEntrada(serializers.Serializer):
    insumo = serializers.IntegerField(help_text="Id de um insumo ATIVO da sua unidade (origem).")
    quantidade = serializers.DecimalField(**QUANTIDADE)
    medida = serializers.IntegerField(required=False, help_text="Unidade da quantidade (padrão: a do insumo).")


class TransferenciaEntrada(serializers.Serializer):
    destino = serializers.IntegerField(help_text="Id de OUTRA unidade da mesma estrutura (matriz ↔ filiais).")
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    itens = ItemTransferenciaEntrada(many=True, allow_empty=False)


class TransferenciaSerializer(serializers.ModelSerializer):
    origem_nome = serializers.CharField(source="origem.nome_razao_social", read_only=True)
    destino_nome = serializers.CharField(source="destino.nome_razao_social", read_only=True)
    itens = serializers.SerializerMethodField()

    class Meta:
        model = TransferenciaEstoque
        fields = ["id", "origem", "origem_nome", "destino", "destino_nome", "responsavel_nome", "observacao", "data", "itens"]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_itens(self, transferencia):
        saidas = [m for m in transferencia.movimentos.all() if m.tipo == "transferencia_saida"]
        return [{"insumo": m.insumo_id, "nome": m.insumo.nome, "quantidade": str(m.quantidade), "unidade": m.insumo.medida.sigla, "lote": m.lote.codigo_exibicao} for m in saidas]


@extend_schema(tags=["estoque"])
class EstoqueTransferenciaViewSet(viewsets.GenericViewSet):
    """Transferências de estoque entre a matriz e as filiais. A origem é SEMPRE a unidade do usuário; o destino precisa ser outra
    unidade ativa da mesma estrutura. Tudo ou nada: saldo insuficiente em qualquer item recusa a transferência inteira (422).
    Exige a permissão específica de transferência (concedida só a quem for autorizado)."""

    serializer_class = TransferenciaSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "transferenciaestoque", "estoque"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TransferenciaEstoque.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        if cliente is None:
            return TransferenciaEstoque.objects.none()
        return TransferenciaEstoque.objects.filter(Q(origem=cliente) | Q(destino=cliente)).select_related("origem", "destino").prefetch_related("movimentos__insumo__medida", "movimentos__lote")

    def get_serializer_class(self):
        return TransferenciaEntrada if self.action == "create" else TransferenciaSerializer

    @extend_schema(responses={200: TransferenciaSerializer(many=True)})
    def list(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(TransferenciaSerializer(pagina, many=True).data)

    @extend_schema(responses={200: TransferenciaSerializer})
    def retrieve(self, request, pk=None):
        return resposta(TransferenciaSerializer(self.get_object()).data)

    @extend_schema(request=TransferenciaEntrada, responses={201: TransferenciaSerializer})
    def create(self, request):
        entrada = TransferenciaEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        origem, destinos = _unidades_da_transferencia(request.user)
        destino = destinos.filter(pk=dados["destino"]).first()
        if destino is None:
            raise serializers.ValidationError({"destino": ["Unidade de destino inválida."]})
        insumos = {i.pk: i for i in Insumo.objects.select_related("medida").filter(cliente=origem, ativo=True, pk__in=[i["insumo"] for i in dados["itens"]])}
        medidas = {m.pk: m for m in Medida.objects.filter(ativo=True)}
        itens = []
        for numero, item in enumerate(dados["itens"], start=1):
            insumo = insumos.get(item["insumo"])
            if insumo is None:
                raise serializers.ValidationError({"itens": [f"Item {numero}: insumo inválido."]})
            medida = medidas.get(item.get("medida")) if item.get("medida") else insumo.medida
            if medida is None:
                raise serializers.ValidationError({"itens": [f"Item {numero}: unidade inválida."]})
            itens.append((insumo, item["quantidade"], medida))
        if len({i.pk for i, _, _ in itens}) != len(itens):
            raise serializers.ValidationError({"itens": ["Um insumo só pode aparecer uma vez na transferência; some as quantidades."]})
        transferencia = transferir(request.user, origem, destino, itens, dados.get("observacao", ""))  # RegraNegocioError -> 422
        return resposta(TransferenciaSerializer(self.get_queryset().get(pk=transferencia.pk)).data, "Transferência registrada.", 201)


# ===================================================================================================
# Financeiro: categorias
# ===================================================================================================

class CategoriaFinanceiraSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaFinanceira
        fields = ["id", "nome", "tipo", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CategoriaFinanceiraEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=100)
    tipo = serializers.ChoiceField(choices=TIPO_CATEGORIA_CHOICES)


@documentar("financeiro", CategoriaFinanceiraSerializer, CategoriaFinanceiraEntrada, [OpenApiParameter("tipo", str, enum=["receita", "despesa"])])
class CategoriaFinanceiraViewSet(AtivarInativarMixin, RecursoViewSet):
    """Categorias de receita e despesa (agrupam o DRE). Só criação (como na tela), ativar/inativar e exclusão: sem contas é apagada;
    em uso é apenas desativada. Não há edição de nome/tipo."""

    modelo, serializer_class, entrada_class, form_class = CategoriaFinanceira, CategoriaFinanceiraSerializer, CategoriaFinanceiraEntrada, CategoriaFinanceiraForm
    permissao_app, permissao_model, modulo_slug = "financeiro", "categoriafinanceira", "financeiro-categorias"
    permissoes_por_acao = {"ativar": "change", "inativar": "change"}
    http_method_names = ["get", "post", "delete", "head", "options"]
    colunas_ordenaveis = {"nome": "nome", "tipo": "tipo"}
    ordenacao_padrao = ["tipo", "nome"]
    rotulo = "Categoria financeira"

    def filtrar(self, queryset, params):
        # a tela mostra TODAS por padrão (ativas e inativas); aqui o filtro `ativo` continua valendo quando enviado
        queryset = queryset if params.get("ativo") is None else super().filtrar(queryset, params)
        termo = (params.get("q") or "").strip()
        queryset = queryset.filter(nome__icontains=termo) if termo and params.get("ativo") is None else queryset
        return queryset.filter(tipo=params["tipo"]) if params.get("tipo") in ("receita", "despesa") else queryset


# ===================================================================================================
# Financeiro: contas a pagar / a receber
# ===================================================================================================

class BaixaSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    tipo = serializers.ChoiceField(choices=["baixa", "estorno"])
    valor = serializers.IntegerField(help_text="Em centavos.")
    data = serializers.DateField()
    observacao = serializers.CharField()
    responsavel_nome = serializers.CharField()
    estorna_baixa = serializers.IntegerField(source="estorno_de_id", allow_null=True)
    estornada = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_estornada(self, baixa):
        return hasattr(baixa, "estornada_por")


class ContaSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    descricao = serializers.CharField()
    contraparte = serializers.CharField(help_text="Fornecedor (a pagar) ou cliente (a receber).")
    categoria = serializers.IntegerField(source="categoria_id")
    categoria_nome = serializers.CharField(source="categoria.nome")
    valor = serializers.IntegerField(help_text="Em centavos.")
    valor_baixado = serializers.IntegerField(help_text="Total líquido das baixas, em centavos.")
    saldo = serializers.IntegerField(help_text="valor − valor_baixado, em centavos.")
    emissao = serializers.DateField()
    vencimento = serializers.DateField()
    observacao = serializers.CharField()
    status = serializers.ChoiceField(choices=["aberta", "parcial", "paga", "cancelada"])
    atrasada = serializers.BooleanField()
    ativo = serializers.BooleanField()
    baixas = BaixaSerializer(many=True)


class ContaEntrada(serializers.Serializer):
    descricao = serializers.CharField(max_length=150)
    contraparte = serializers.CharField(max_length=150, required=False, allow_blank=True)
    categoria = serializers.IntegerField(help_text="Id de uma categoria financeira ATIVA do tipo certo (despesa para contas a pagar, receita para a receber).")
    valor = serializers.IntegerField(min_value=1, max_value=10**12, help_text="Em centavos (R$ 1.250,00 = 125000).")
    emissao = serializers.DateField(required=False, help_text="Padrão: hoje.")
    vencimento = serializers.DateField()
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


class BaixaEntrada(serializers.Serializer):
    valor = serializers.IntegerField(min_value=1, max_value=10**12, help_text="Em centavos; não pode passar do saldo.")
    data = serializers.DateField(required=False, help_text="Padrão: hoje; não pode ser futura.")
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


class EstornoEntrada(serializers.Serializer):
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


SITUACOES = ["aberta", "parcial", "paga", "atrasada", "cancelada"]


class ContaViewSetBase(RecursoViewSet):
    """Conta a pagar ou a receber (a subclasse define `chave`). Cancelar/excluir só sem baixas; conta cancelada não se edita."""

    chave = None
    entrada_class = ContaEntrada
    serializer_class = ContaSerializer
    campos_moeda = ("valor",)
    campos_busca = ("descricao", "contraparte", "categoria__nome")
    colunas_ordenaveis = {"vencimento": "vencimento", "descricao": "descricao", "contraparte": "contraparte", "categoria": "categoria__nome", "valor": "valor"}
    ordenacao_padrao = ["vencimento", "pk"]
    rotulo = "Conta"
    permissoes_por_acao = {"baixas": "change", "estorno": "change"}

    @property
    def padroes(self):
        return {"contraparte": "", "observacao": "", "emissao": timezone.localdate()}  # emissão padrão: hoje

    @property
    def modelo(self):
        return CONFIG[self.chave][0]

    @property
    def form_class(self):
        return conta_form_para(self.modelo, CONFIG[self.chave][3])

    def consulta_base(self):
        return self.modelo.objects.select_related("categoria").prefetch_related("baixas__estornada_por")

    def get_queryset(self):
        queryset = super().get_queryset()
        # conta cancelada não se edita nem recebe baixas
        return queryset.filter(ativo=True) if self.action in ("update", "partial_update", "baixas", "estorno") else queryset

    def filtrar(self, queryset, params):
        """Mesmos filtros da tela: texto, categoria, situação e período de vencimento (sem situação = só contas ativas)."""
        termo = (params.get("q") or "").strip()
        if termo:
            queryset = queryset.filter(Q(descricao__icontains=termo) | Q(contraparte__icontains=termo) | Q(categoria__nome__icontains=termo))
        if params.get("categoria", "").isdigit():
            queryset = queryset.filter(categoria_id=params["categoria"])
        situacao = params.get("situacao", "")
        if situacao == "cancelada":
            queryset = queryset.filter(ativo=False)
        else:
            queryset = queryset.filter(ativo=True)
            if situacao == "aberta":
                queryset = queryset.filter(valor_baixado=0)
            elif situacao == "parcial":
                queryset = queryset.filter(valor_baixado__gt=0, valor_baixado__lt=F("valor"))
            elif situacao == "paga":
                queryset = queryset.filter(valor_baixado=F("valor"))
            elif situacao == "atrasada":
                queryset = queryset.filter(valor_baixado__lt=F("valor"), vencimento__lt=timezone.localdate())
        de, ate = parse_date(params.get("vencimento_de", "")), parse_date(params.get("vencimento_ate", ""))
        if de:
            queryset = queryset.filter(vencimento__gte=de)
        if ate:
            queryset = queryset.filter(vencimento__lte=ate)
        return queryset

    def dados_atuais(self, c):
        return {"descricao": c.descricao, "contraparte": c.contraparte, "categoria": c.categoria_id, "valor": c.valor,
                "emissao": c.emissao, "vencimento": c.vencimento, "observacao": c.observacao}

    def destroy(self, request, *args, **kwargs):
        conta = self.get_object()
        if conta.valor_baixado > 0:
            raise RegraNegocioError("A conta tem baixas registradas. Estorne as baixas antes de excluí-la ou cancelá-la.")
        return super().destroy(request, *args, **kwargs)  # sem uso: apaga; com histórico de baixas/estornos: só cancela

    def _devolver(self, conta, mensagem, codigo=200):
        conta = self.consulta_base().get(pk=conta.pk)
        return resposta(ContaSerializer(conta).data, mensagem, codigo)

    @extend_schema(request=BaixaEntrada, responses={201: ContaSerializer}, summary="Registra a baixa (pagamento/recebimento, total ou parcial)",
                   parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH, description="Id da conta.")])
    @action(detail=True, methods=["post"], url_path="baixas")
    def baixas(self, request, pk=None):
        conta = self.get_object()
        entrada = BaixaEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        form = BaixaForm({"valor": reais(dados["valor"]), "data": (dados.get("data") or timezone.localdate()).isoformat(), "observacao": dados.get("observacao", "")})
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        registrar_baixa(request.user, conta, form.cleaned_data["valor"], form.cleaned_data["data"], form.cleaned_data["observacao"])  # 422 se passar do saldo
        return self._devolver(conta, "Baixa registrada.", 201)

    @extend_schema(request=EstornoEntrada, responses={200: ContaSerializer}, summary="Estorna uma baixa (cria um lançamento de estorno)",
                   parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH, description="Id da conta."), OpenApiParameter("baixa_pk", int, OpenApiParameter.PATH, description="Id da baixa a estornar.")])
    @action(detail=True, methods=["post"], url_path=r"baixas/(?P<baixa_pk>\d+)/estorno")
    def estorno(self, request, pk=None, baixa_pk=None):
        conta = self.get_object()
        baixa = get_object_or_404(conta.baixas.all(), pk=baixa_pk)
        entrada = EstornoEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        estornar_baixa(request.user, baixa, (entrada.validated_data.get("observacao") or "").strip())
        return self._devolver(conta, "Baixa estornada.")


def _contas(chave, modulo, modelo, rotulo):
    atributos = {"chave": chave, "permissao_app": "financeiro", "permissao_model": modelo, "modulo_slug": modulo, "rotulo": rotulo}
    classe = type(f"Conta{chave.title()}ViewSet", (ContaViewSetBase,), atributos)
    classe.__doc__ = (
        f"{rotulo}s da empresa do usuário, com baixas (pagamentos/recebimentos, totais ou parciais) e estornos imutáveis. "
        "Filtros: q, categoria, situacao (aberta, parcial, paga, atrasada, cancelada), vencimento_de, vencimento_ate. "
        "DELETE cancela/exclui a conta sem baixas (estorne antes)."
    )
    filtros = [OpenApiParameter("categoria", int), OpenApiParameter("situacao", str, enum=SITUACOES), OpenApiParameter("vencimento_de", str, description="AAAA-MM-DD"),
               OpenApiParameter("vencimento_ate", str, description="AAAA-MM-DD"), OpenApiParameter("ordenar", str, description="vencimento, descricao, contraparte, categoria, valor")]
    return documentar("financeiro", ContaSerializer, ContaEntrada, filtros)(classe)


ContaPagarViewSet = _contas("pagar", "financeiro-pagar", "contapagar", "Conta a pagar")
ContaReceberViewSet = _contas("receber", "financeiro-receber", "contareceber", "Conta a receber")


# ===================================================================================================
# Financeiro: DRE e balanço (gerenciais) e painel
# ===================================================================================================

class _Relatorio(viewsets.ViewSet):
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "financeiro", "relatoriofinanceiro", "financeiro-relatorios"


@extend_schema(tags=["financeiro"])
class DreViewSet(_Relatorio):
    """DRE GERENCIAL da empresa do usuário (não é demonstração contábil oficial). Valores em centavos."""

    @extend_schema(parameters=[OpenApiParameter("inicio", str, description="AAAA-MM-DD (padrão: início do mês)"), OpenApiParameter("fim", str, description="AAAA-MM-DD (padrão: fim do mês)"),
                               OpenApiParameter("regime", str, enum=["caixa", "vencimento"], description="caixa = pagamentos/recebimentos; vencimento = contas")],
                   responses={200: inline_serializer("Dre", {"regime": serializers.CharField(), "inicio": serializers.DateField(), "fim": serializers.DateField(),
                                                             "receitas": inline_serializer("DreReceita", {"categoria": serializers.CharField(), "total": serializers.IntegerField()}, many=True),
                                                             "despesas": inline_serializer("DreDespesa", {"categoria": serializers.CharField(), "total": serializers.IntegerField()}, many=True),
                                                             "total_receitas": serializers.IntegerField(), "total_despesas": serializers.IntegerField(), "resultado": serializers.IntegerField()})})
    def list(self, request):
        cliente = _cliente_ou_erro(request)
        form = PeriodoForm(request.query_params or None)
        inicio, fim, regime = form.periodo()
        r = dre(cliente, inicio, fim, regime)  # 422 se início > fim
        linhas = lambda itens: [{"categoria": nome, "total": total} for nome, total in itens]  # noqa: E731
        return resposta({**r, "receitas": linhas(r["receitas"]), "despesas": linhas(r["despesas"])})


@extend_schema(tags=["financeiro"])
class BalancoViewSet(_Relatorio):
    """Balanço GERENCIAL simplificado da empresa do usuário numa data (não é balanço contábil oficial). Valores em centavos."""

    @extend_schema(parameters=[OpenApiParameter("data", str, description="AAAA-MM-DD (padrão: hoje)")],
                   responses={200: inline_serializer("Balanco", {k: serializers.IntegerField() for k in ("caixa", "a_receber", "estoque", "ativo", "a_pagar", "passivo", "patrimonio")} | {"data": serializers.DateField()})})
    def list(self, request):
        return resposta(balanco(_cliente_ou_erro(request), parse_date(request.query_params.get("data", "")) or timezone.localdate()))


@extend_schema(tags=["painel"])
class PainelViewSet(viewsets.ViewSet):
    """Indicadores do painel da empresa do usuário num período. Cada bloco só aparece se o módulo está habilitado e o usuário tem a permissão."""

    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[OpenApiParameter("periodo", str, enum=[p for p, _ in PRESETS], description="Padrão: mes_atual"), OpenApiParameter("inicio", str, description="AAAA-MM-DD (periodo=personalizado)"),
                               OpenApiParameter("fim", str, description="AAAA-MM-DD (periodo=personalizado)")],
                   responses={200: inline_serializer("Painel", {"periodo": inline_serializer("PeriodoDoPainel", {"inicio": serializers.DateField(), "fim": serializers.DateField(), "tipo": serializers.CharField(), "rotulo": serializers.CharField()}),
                                                                  "blocos": serializers.DictField(help_text="resultado, contas, estoque, cadastros, precificacao e categorias (conforme as permissões).")})})
    def list(self, request):
        cliente = _cliente_ou_erro(request)
        params = request.query_params
        inicio, fim, preset, rotulo = periodo(params.get("periodo", "mes_atual"), parse_date(params.get("inicio", "")), parse_date(params.get("fim", "")))
        return resposta({"periodo": {"inicio": inicio, "fim": fim, "tipo": preset, "rotulo": rotulo}, "blocos": indicadores(request.user, cliente, inicio, fim)})
