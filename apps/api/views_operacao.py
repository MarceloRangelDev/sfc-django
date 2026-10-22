"""Recursos operacionais da API v1: custos comuns (CRUD), movimentações de estoque (consulta e edição)
e a distribuição do custo operacional por categoria (consulta e ajuste manual).

Mesmas regras das telas: autenticação por token, permissão nativa do Django por ação, módulo habilitado
para o cliente e isolamento por cliente aplicado no servidor (o cliente vem do usuário, nunca do corpo
nem da querystring)."""
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action

from apps.auditoria.utils import registrar_exclusao
from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.custo.models import TIPO_FIXO, TIPO_VARIAVEL, CustoModelo
from apps.custo.services import ajustar_categoria, distribuicao_por_categoria, restaurar_categoria
from apps.estoque.forms import MovimentoForm
from apps.estoque.models import MovimentoEstoque
from apps.estoque.services import MANTER_VALIDADE, editar_movimento, registrar_entrada, registrar_saida
from apps.insumo.models import Insumo
from web_project.acesso import administrador_global, cliente_do_usuario
from web_project.mixins import em_uso

from .negocio import erros_de
from .permissions import PermissaoDoSistema
from .serializers_operacao import (
    MovimentoEstoqueCriacaoSerializer, CustoCategoriaAjusteSerializer, CustoCategoriaSerializer, CustoComumSerializer, CustoDistribuicaoSerializer, MovimentoEstoqueEdicaoSerializer,
    MovimentoEstoqueSerializer, formatar,
)
from .views import PARAMETRO_ATIVO, PARAMETROS_BUSCA, AuditoriaViewSetMixin, _filtro_ativo, _ordenar, resposta


@extend_schema(tags=["custos-comuns"])
class CustoComumViewSet(AuditoriaViewSetMixin, viewsets.ModelViewSet):
    """Custos comuns usados no Custo operacional: os PADRÕES do sistema e os próprios da empresa do
    usuário. Criar/editar/excluir atinge os próprios; os padrões (valem para todas as empresas) só o
    administrador do sistema altera. Um custo comum já usado em algum custo operacional é apenas
    desativado ao excluir (DELETE)."""

    serializer_class = CustoComumSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "custo", "customodelo", "custo-comum"
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CustoModelo.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        proprios = Q(cliente=cliente) if cliente else Q(pk__in=[])
        if self.action in ("update", "partial_update", "destroy"):
            # Padrões do sistema (cliente nulo) só o administrador global altera, como nas telas.
            queryset = CustoModelo.objects.filter(proprios | Q(cliente__isnull=True)) if administrador_global(self.request.user) else CustoModelo.objects.filter(proprios)
        else:
            queryset = CustoModelo.objects.filter(proprios | Q(cliente__isnull=True))
        params = self.request.query_params
        if self.action == "list":
            queryset = _filtro_ativo(queryset, params)
            if params.get("q"):
                queryset = queryset.filter(Q(nome__icontains=params["q"].strip()) | Q(descricao__icontains=params["q"].strip()))
            if params.get("tipo") in (TIPO_FIXO, TIPO_VARIAVEL):
                queryset = queryset.filter(tipo=params["tipo"])
            if params.get("origem") == "sistema":
                queryset = queryset.filter(cliente__isnull=True)
            elif params.get("origem") == "proprio":
                queryset = queryset.filter(cliente__isnull=False)
        return _ordenar(queryset, params, {"nome": "nome", "tipo": "tipo", "criado_em": "criado_em"}, ["nome"])

    def dados_de_criacao(self):
        return {"cliente": cliente_do_usuario(self.request.user)}  # nunca vem do corpo da requisição

    def perform_destroy(self, comum):
        with transaction.atomic():
            registrar_exclusao(self.request, comum, logica=em_uso(comum))
            if em_uso(comum):
                comum.ativo = False
                comum.save(update_fields=["ativo", "atualizado_em"])
            else:
                comum.delete()

    @extend_schema(parameters=[
        *PARAMETROS_BUSCA, PARAMETRO_ATIVO, OpenApiParameter("tipo", str, enum=[TIPO_FIXO, TIPO_VARIAVEL]),
        OpenApiParameter("origem", str, enum=["sistema", "proprio"]),
        OpenApiParameter("ordenar", str, description="nome, tipo, criado_em (prefixo - = decrescente)"),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["estoque"])
class MovimentoEstoqueViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Movimentações de estoque do cliente do usuário. Consulta e CORREÇÃO (PUT/PATCH): a quantidade, a
    unidade digitada, a data, a observação e — nas entradas — a validade podem ser editadas; os saldos
    do lote e o histórico posterior são recalculados e a edição é recusada (422) se algum saldo ficasse
    negativo. Movimentos de transferência não são editáveis. POST registra entrada, saída ou ajuste; não há exclusão."""

    serializer_class = MovimentoEstoqueSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "estoque", "movimentoestoque", "estoque"
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MovimentoEstoque.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        queryset = MovimentoEstoque.objects.select_related("insumo", "insumo__medida", "lote").filter(cliente=cliente) if cliente else MovimentoEstoque.objects.none()
        params = self.request.query_params
        if self.action == "list":
            if params.get("insumo", "").isdigit():
                queryset = queryset.filter(insumo_id=params["insumo"])
            if params.get("tipo") in dict(MovimentoEstoque.TIPO_CHOICES):
                queryset = queryset.filter(tipo=params["tipo"])
            de, ate = parse_date(params.get("data_de", "")), parse_date(params.get("data_ate", ""))
            if de:
                queryset = queryset.filter(data__date__gte=de)
            if ate:
                queryset = queryset.filter(data__date__lte=ate)
            if params.get("q"):
                termo = params["q"].strip()
                queryset = queryset.filter(Q(insumo__nome__icontains=termo) | Q(lote__codigo__icontains=termo) | Q(responsavel_nome__icontains=termo))
        return _ordenar(queryset, params, {"data": "data", "quantidade": "quantidade", "insumo": "insumo__nome"}, ["-data", "-pk"])

    @extend_schema(parameters=[
        OpenApiParameter("insumo", int), OpenApiParameter("tipo", str, enum=[t for t, _ in MovimentoEstoque.TIPO_CHOICES]),
        OpenApiParameter("data_de", str, description="AAAA-MM-DD"), OpenApiParameter("data_ate", str, description="AAAA-MM-DD"),
        *PARAMETROS_BUSCA, OpenApiParameter("ordenar", str, description="data, quantidade, insumo (prefixo - = decrescente; padrão: mais recentes)"),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=MovimentoEstoqueCriacaoSerializer, responses={201: MovimentoEstoqueSerializer(many=True)},
        examples=[OpenApiExample("Entrada com validade e sem lote", value={"insumo": 1, "tipo": "entrada", "quantidade": "10", "validade": "2026-12-31"}, request_only=True)],
        description="Registra entrada, saída ou ajuste na unidade do usuário. Saídas e reduções consomem o lote que vence primeiro (podem gerar "
                    "mais de um movimento). A validade pode ser informada sem lote. Erros: 400 (campos), 422 (saldo insuficiente, unidade incompatível).",
    )
    def create(self, request, *args, **kwargs):
        entrada = MovimentoEstoqueCriacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        cliente = cliente_do_usuario(request.user)
        insumo = Insumo.objects.filter(cliente=cliente, ativo=True, pk=dados["insumo"]).select_related("medida").first() if cliente else None
        # o formulário da tela valida tudo (insumo da empresa, unidade, lote/validade só em entradas...)
        form = MovimentoForm(
            {"insumo": dados["insumo"], "tipo": dados["tipo"], "quantidade": str(dados["quantidade"]), "medida": dados.get("medida") or (insumo.medida_id if insumo else ""),
             "lote": dados.get("lote", ""), "validade": dados["validade"].isoformat() if dados.get("validade") else "", "motivo": dados.get("motivo", ""),
             "observacao": dados.get("observacao", "")},
            cliente=cliente,
        )
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        d = form.cleaned_data
        if d["tipo"] in (MovimentoEstoque.ENTRADA, MovimentoEstoque.AJUSTE_ENTRADA):
            movimentos = [registrar_entrada(request.user, d["insumo"], d["quantidade"], d["medida"], d["lote"], d["validade"], d["observacao"], tipo=d["tipo"])]
        else:
            movimentos = registrar_saida(request.user, d["insumo"], d["quantidade"], d["medida"], d["observacao"], tipo=d["tipo"], motivo=d["motivo"])
        ids = [m.pk for m in movimentos]
        salvos = MovimentoEstoque.objects.select_related("insumo", "insumo__medida", "lote").filter(pk__in=ids).order_by("pk")
        return resposta(MovimentoEstoqueSerializer(salvos, many=True).data, "Movimentação registrada.", 201)

    @extend_schema(
        request=MovimentoEstoqueEdicaoSerializer, responses={200: MovimentoEstoqueSerializer},
        examples=[OpenApiExample("Corrigir quantidade", value={"quantidade": "12", "observacao": "conferido no inventário"}, request_only=True)],
        description="Corrige a movimentação e recalcula os saldos. Erros: 404 (outra empresa), 422 (saldo negativo, transferência, validade).",
    )
    def update(self, request, *args, **kwargs):
        movimento = self.get_object()
        entrada = MovimentoEstoqueEdicaoSerializer(data=request.data, partial=kwargs.get("partial", False))
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        if "quantidade" not in dados and not kwargs.get("partial", False):
            raise serializers.ValidationError({"quantidade": ["Este campo é obrigatório."]})
        editar_movimento(
            request, movimento.pk, Cliente.objects.filter(pk=movimento.cliente_id),
            dados.get("quantidade", movimento.quantidade), dados.get("medida", movimento.insumo.medida), dados.get("data", movimento.data),
            dados.get("observacao", movimento.observacao), dados["validade"] if "validade" in dados else MANTER_VALIDADE,
        )
        movimento.refresh_from_db()
        return resposta(MovimentoEstoqueSerializer(movimento, context=self.get_serializer_context()).data, "Movimentação atualizada e saldos recalculados.")


@extend_schema(tags=["custo-operacional"])
class CustoCategoriaViewSet(viewsets.ViewSet):
    """Distribuição do custo operacional entre as categorias, em centavos: calculada pelas vendas de cada
    categoria e ajustável manualmente. A soma sempre fecha com o custo operacional total cadastrado.
    PUT em /{categoria}/ajuste/ fixa o valor de uma categoria (as demais se recalculam); DELETE volta ao
    cálculo automático. O ajuste nunca é sobrescrito sem o usuário pedir."""

    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "custo", "custooperacional", "custo-operacional"
    permissoes_por_acao = {"ajuste": "change"}

    def _cliente(self, request):
        cliente = cliente_do_usuario(request.user)
        if cliente is None:
            raise serializers.ValidationError("Usuário sem empresa vinculada.")
        return cliente

    @extend_schema(
        responses={200: CustoDistribuicaoSerializer},
        examples=[OpenApiExample("Exemplo", value={
            "total": 30000, "distribuido": 30000, "nao_distribuido": 0, "excedente_manual": 0, "sem_historico": False,
            "categorias": [{
                "id": 1, "nome": "Doces", "ativa": True, "quantidade_vendida": 100, "valor": 10000, "valor_formatado": "R$ 100,00",
                "por_unidade": 100, "origem": "vendas", "ajuste_manual": False,
            }],
        }, response_only=True)],
    )
    def list(self, request):
        linhas, totais = distribuicao_por_categoria(self._cliente(request))
        categorias = [
            {
                "id": l["categoria"].pk, "nome": l["categoria"].nome, "ativa": l["categoria"].ativo, "quantidade_vendida": l["quantidade"],
                "valor": l["valor"], "valor_formatado": formatar(l["valor"]), "por_unidade": l["por_unidade"], "origem": l["origem"],
                "ajuste_manual": l["manual"],
            }
            for l in linhas
        ]
        dados = {
            "total": totais["mensal"], "distribuido": totais["distribuido"], "nao_distribuido": totais["nao_distribuido"],
            "excedente_manual": totais["excedente_manual"], "sem_historico": totais["sem_historico"], "categorias": categorias,
        }
        return resposta(CustoDistribuicaoSerializer(dados).data)

    @extend_schema(parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH, description="Id da categoria.")])
    @extend_schema(
        methods=["PUT"], request=CustoCategoriaAjusteSerializer, responses={200: CustoCategoriaSerializer},
        summary="Fixa manualmente o custo operacional da categoria",
    )
    @extend_schema(methods=["DELETE"], request=None, responses={200: CustoCategoriaSerializer}, summary="Restaura o cálculo automático da categoria")
    @action(detail=True, methods=["put", "delete"], url_path="ajuste")
    def ajuste(self, request, pk=None):
        cliente = self._cliente(request)
        categoria = get_object_or_404(Categoria, pk=pk, cliente=cliente)  # categoria de outra empresa = 404
        if request.method == "PUT":
            entrada = CustoCategoriaAjusteSerializer(data=request.data)
            entrada.is_valid(raise_exception=True)
            ajustar_categoria(request, cliente, categoria, entrada.validated_data["valor"])
        else:
            restaurar_categoria(request, cliente, categoria)
        linhas, _ = distribuicao_por_categoria(cliente)
        l = next(l for l in linhas if l["categoria"].pk == categoria.pk)
        dados = {
            "id": categoria.pk, "nome": categoria.nome, "ativa": categoria.ativo, "quantidade_vendida": l["quantidade"], "valor": l["valor"],
            "valor_formatado": formatar(l["valor"]), "por_unidade": l["por_unidade"], "origem": l["origem"], "ajuste_manual": l["manual"],
        }
        return resposta(CustoCategoriaSerializer(dados).data, "Custo operacional da categoria atualizado.")
