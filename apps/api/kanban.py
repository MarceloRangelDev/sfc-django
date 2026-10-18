"""Kanban na API v1 (somente administrador global; a regra vive em apps/kanban/services.py e vale para tela e API).

Tenant derivado do usuário autenticado. Cartão/coluna de outro cliente => 404. Regra de negócio violada => 422.
"""
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.views import APIView

from apps.kanban import services
from web_project.regras import RegraNegocioError

from .pdv import PermissaoPorAcao
from .views import resposta

TAG = ["kanban"]


def _prazo(valor):
    if valor in (None, ""):
        return None
    prazo = parse_date(str(valor))
    if prazo is None:
        raise RegraNegocioError("Prazo inválido (use AAAA-MM-DD).")
    return prazo


class _Base(APIView):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "kanban"


@extend_schema(tags=TAG, responses={200: OpenApiTypes.OBJECT})
class QuadroView(_Base):
    """Quadro do cliente do administrador: colunas em ordem, cada uma com seus cartões."""

    permissoes = {"get": "kanban.view_cartao"}

    def get(self, request):
        return resposta([services.serializar(c) for c in services.quadro(request.user)])


@extend_schema(tags=TAG)
class ColunasView(_Base):
    permissoes = {"post": "kanban.add_coluna"}

    @extend_schema(request=inline_serializer("ColunaEntrada", {"nome": serializers.CharField(max_length=80)}), responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        coluna = services.criar_coluna(request, request.data.get("nome"))
        return resposta({"id": coluna.pk, "nome": coluna.nome}, "Coluna criada.", 201)


@extend_schema(tags=TAG)
class ColunaView(_Base):
    permissoes = {"patch": "kanban.change_coluna", "delete": "kanban.delete_coluna"}

    @extend_schema(request=inline_serializer("ColunaRenomear", {"nome": serializers.CharField(max_length=80)}), responses={200: OpenApiTypes.OBJECT})
    def patch(self, request, pk):
        coluna = services.renomear_coluna(request, pk, request.data.get("nome"))
        return resposta({"id": coluna.pk, "nome": coluna.nome})

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk):
        services.excluir_coluna(request, pk)
        return resposta(None, "Coluna excluída.")


_ENTRADA = {"titulo": serializers.CharField(max_length=150), "descricao": serializers.CharField(max_length=2000, required=False),
            "responsavel": serializers.IntegerField(required=False, allow_null=True), "prazo": serializers.DateField(required=False, allow_null=True)}


@extend_schema(tags=TAG)
class CartoesView(_Base):
    permissoes = {"post": "kanban.add_cartao"}

    @extend_schema(request=inline_serializer("CartaoCriar", {"coluna": serializers.IntegerField(), **_ENTRADA}), responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        d = request.data
        c = services.criar_cartao(request, d.get("coluna"), d.get("titulo"), d.get("descricao"), d.get("responsavel"), _prazo(d.get("prazo")))
        return resposta({"id": c.pk}, "Cartão criado.", 201)


@extend_schema(tags=TAG)
class CartaoView(_Base):
    permissoes = {"patch": "kanban.change_cartao", "delete": "kanban.delete_cartao"}

    @extend_schema(request=inline_serializer("CartaoEditar", _ENTRADA), responses={200: OpenApiTypes.OBJECT})
    def patch(self, request, pk):
        d = request.data
        c = services.editar_cartao(request, pk, d.get("titulo"), d.get("descricao"), d.get("responsavel"), _prazo(d.get("prazo")))
        return resposta({"id": c.pk})

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk):
        services.excluir_cartao(request, pk)
        return resposta(None, "Cartão excluído.")


@extend_schema(tags=TAG)
class CartaoMoverView(_Base):
    """Move o cartão para outra coluna/posição (0 = topo). Auditado."""

    permissoes = {"post": "kanban.mover_cartao"}

    @extend_schema(request=inline_serializer("CartaoMover", {"coluna": serializers.IntegerField(), "posicao": serializers.IntegerField(min_value=0)}), responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        c = services.mover_cartao(request, pk, request.data.get("coluna"), request.data.get("posicao", 0))
        return resposta({"id": c.pk, "coluna": c.coluna_id, "ordem": c.ordem})
