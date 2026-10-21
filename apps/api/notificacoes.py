"""Caixa de notificações do PRÓPRIO usuário na API v1 (para o app mobile): listar, ver o resumo do sino e marcar como lida.

Espelha as telas `/notificacoes/minhas`, `/notificacoes/resumo` e `/notificacoes/<id>/abrir`, com as mesmas regras:
- só login (cada pessoa alcança apenas as próprias notificações; não há parâmetro de usuário, então não dá para ler a caixa de outra);
- as lidas há 48 h ou mais já não aparecem (`vigentes()`), mesmo que a limpeza agendada ainda não tenha rodado; outro id responde 404;
- enviar notificações continua só pela tela (exige módulo + permissão e confirma a quantidade de destinatários).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound

from apps.notificacao import services
from apps.notificacao.models import DestinatarioNotificacao

from .pagination import PaginacaoPadrao
from .views import resposta


def _dict(item):
    n = item.notificacao
    return {"id": item.pk, "titulo": n.titulo, "mensagem": n.mensagem, "link": n.link, "lida": item.lida_em is not None, "lida_em": item.lida_em, "criada_em": item.criado_em}


@extend_schema(tags=["notificacoes"])
class MinhasNotificacoesViewSet(viewsets.ViewSet):
    """Notificações do usuário autenticado (lidas somem 48 horas depois de lidas)."""

    def _escopo(self, request):
        return DestinatarioNotificacao.objects.vigentes().filter(usuario=request.user).select_related("notificacao")

    @extend_schema(parameters=[OpenApiParameter("situacao", str, description="`lidas` ou `nao-lidas`")], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        consulta = self._escopo(request)
        if request.query_params.get("situacao") == "nao-lidas":
            consulta = consulta.filter(lida_em__isnull=True)
        elif request.query_params.get("situacao") == "lidas":
            consulta = consulta.filter(lida_em__isnull=False)
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_dict(i) for i in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Contador de não lidas e as mais recentes (o mesmo do sino da navbar)")
    @action(detail=False, methods=["get"], url_path="resumo")
    def resumo(self, request):
        nao_lidas, recentes = services.resumo_do_usuario(request.user)
        return resposta({"nao_lidas": nao_lidas, "recentes": [_dict(i) for i in recentes]})

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Marca uma notificação como lida (inicia o prazo de 48 h)")
    @action(detail=True, methods=["post"], url_path="ler")
    def ler(self, request, pk=None):
        try:
            item = self._escopo(request).get(pk=pk)
        except (DestinatarioNotificacao.DoesNotExist, ValueError):
            raise NotFound from None
        return resposta(_dict(services.marcar_como_lida(item)))

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Marca todas as suas notificações como lidas")
    @action(detail=False, methods=["post"], url_path="ler-todas")
    def ler_todas(self, request):
        services.marcar_todas_como_lidas(request.user)
        return resposta(None, "Todas as notificações foram marcadas como lidas.")
