"""Pedidos e cozinha na API v1 (aplicativo móvel e integrações futuras).

- POST /pedidos/ cria o pedido e a venda aberta por trás dele (preço do servidor; exige caixa aberto do usuário) e é
  IDEMPOTENTE (`chave` ou cabeçalho `Idempotency-Key`: repetir devolve o MESMO pedido, sem duplicar itens/estoque).
- A regra de confirmação (lançado / pagamento / manual) é da unidade; a baixa de estoque acontece na confirmação, uma vez.
- Mover a fila exige `mover_pedido` (Operador, Gestor e Administrador); cancelar exige `cancelar_pedido`.
- Pedido de outra unidade = 404. Pagamento do pedido: `/pdv/comandas/{venda}/pagar/` (a venda do pedido).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound

from apps.pedido import services as s
from apps.pedido.models import Pedido
from apps.pedido.views import pedido_dict, pedidos_visiveis
from apps.pdv.models import Venda
from apps.pdvconfig.models import Mesa
from web_project.acesso import cliente_do_usuario

from .pagination import PaginacaoPadrao
from .pdv import PdvItemEntrada, PermissaoPorAcao, _caixa_do_usuario, _chave, _consumidor
from .views import resposta


def _detalhe(pedido):
    dados = pedido_dict(pedido)
    dados.update(
        venda=pedido.venda_id, venda_numero=pedido.venda.numero, venda_status=pedido.venda.status, confirmado_em=pedido.confirmado_em, preparo_em=pedido.preparo_em,
        pronto_em=pedido.pronto_em, entregue_em=pedido.entregue_em, cancelado_em=pedido.cancelado_em, motivo_cancelamento=pedido.motivo_cancelamento,
        eventos=[{"de": e.de_status, "para": e.para_status, "usuario": e.usuario_nome, "nota": e.nota, "quando": e.criado_em} for e in pedido.eventos.all()],
    )
    return dados


class PedidoEntrada(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=[c for c, _ in Pedido.TIPO_CHOICES], required=False, help_text="balcao (padrão), mesa, retirada ou entrega.")
    itens = PdvItemEntrada(many=True)
    mesa = serializers.IntegerField(required=False, allow_null=True)
    consumidor = serializers.IntegerField(required=False, allow_null=True)
    observacao = serializers.CharField(required=False, max_length=255)
    chave = serializers.CharField(required=False, help_text="Chave de idempotência (ou cabeçalho Idempotency-Key).")


@extend_schema(tags=["pedidos"])
class PedidoViewSet(viewsets.ViewSet):
    """Pedidos de produção: criar, listar, fila da cozinha, mover e cancelar."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pedido"
    permissoes = {
        "list": "pedido.view_pedido", "retrieve": "pedido.view_pedido", "fila": "pedido.view_pedido", "create": ["pedido.add_pedido", "pdv.add_venda"],
        "confirmar": "pedido.mover_pedido", "avancar": "pedido.mover_pedido", "voltar": "pedido.mover_pedido", "cancelar": "pedido.cancelar_pedido",
    }

    def _pedido(self, request, pk):
        try:
            return pedidos_visiveis(request.user).select_related("venda", "venda__mesa", "venda__consumidor").get(pk=pk)
        except Pedido.DoesNotExist:
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("status", str), OpenApiParameter("q", int, description="número do pedido")], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        consulta = pedidos_visiveis(request.user).select_related("venda", "venda__mesa", "venda__consumidor")
        if request.query_params.get("status") in dict(Pedido.STATUS_CHOICES):
            consulta = consulta.filter(status=request.query_params["status"])
        if request.query_params.get("q", "").lstrip("#").isdigit():
            consulta = consulta.filter(numero=int(request.query_params["q"].lstrip("#")))
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([pedido_dict(p) for p in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_detalhe(self._pedido(request, pk)))

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Fila de produção (confirmado, em preparo, pronto)")
    @action(detail=False, methods=["get"], url_path="fila")
    def fila(self, request):
        from web_project.acesso import clientes_dos_itens

        return resposta([pedido_dict(p) for p in s.fila(clientes_dos_itens(request.user, "view"))])

    @extend_schema(request=PedidoEntrada, responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT}, summary="Cria um pedido (idempotente)")
    def create(self, request):
        caixa = _caixa_do_usuario(request)
        dados = request.data if isinstance(request.data, dict) else {}
        mesa = None
        if dados.get("mesa") not in (None, ""):
            try:
                mesa = Mesa.objects.get(pk=int(dados["mesa"]), cliente=caixa.cliente)
            except (Mesa.DoesNotExist, TypeError, ValueError):
                raise serializers.ValidationError({"mesa": ["Mesa inválida."]}) from None
        tipo = dados.get("tipo") or (Pedido.MESA if mesa else Pedido.BALCAO)
        pedido, criado = s.criar_pedido_avulso(
            request, caixa, dados.get("itens"), tipo, mesa=mesa, consumidor=_consumidor(caixa, dados.get("consumidor")),
            observacao=str(dados.get("observacao") or ""), chave=_chave(request),
        )
        pedido = self._pedido(request, pedido.pk)
        return resposta({**_detalhe(pedido), "criado": criado}, "Pedido criado." if criado else "Pedido já registrado (chave repetida).", 201 if criado else 200)

    def _mover(self, request, pk, funcao, mensagem):
        pedido = funcao(request, self._pedido(request, pk))
        return resposta(_detalhe(self._pedido(request, pedido.pk)), mensagem)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="confirmar")
    def confirmar(self, request, pk=None):
        return self._mover(request, pk, s.confirmar, "Pedido confirmado.")

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Avança um estado: novo→confirmado→em preparo→pronto→entregue")
    @action(detail=True, methods=["post"], url_path="avancar")
    def avancar(self, request, pk=None):
        return self._mover(request, pk, s.avancar, "Pedido atualizado.")

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Volta um passo (pronto→em preparo, em preparo→confirmado)")
    @action(detail=True, methods=["post"], url_path="voltar")
    def voltar(self, request, pk=None):
        return self._mover(request, pk, s.voltar, "Pedido atualizado.")

    @extend_schema(request=inline_serializer("CancelarPedido", {"motivo": serializers.CharField()}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        pedido = s.cancelar(request, self._pedido(request, pk), str(request.data.get("motivo", "")))
        return resposta(_detalhe(self._pedido(request, pedido.pk)), "Pedido cancelado.")
