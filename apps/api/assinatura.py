"""Assinatura (Pagar.me) na API v1. Tenant sempre derivado do usuário autenticado; valores em centavos.

Bloqueio por falta de pagamento: os demais módulos respondem 403 com a mensagem "Acesso bloqueado por falta de pagamento da
assinatura" (ver web_project/acesso.py::motivo_modulo_indisponivel); estes endpoints continuam disponíveis para regularizar.
O webhook do Pagar.me NÃO faz parte desta API/Swagger: é público e autenticado por segredo na URL (README_PAGARME.md).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.views import APIView

from apps.assinatura import pagarme, services
from apps.assinatura.models import Assinatura
from web_project.regras import RegraNegocioError

from .pdv import PermissaoPorAcao
from .views import resposta

TAG = ["assinatura"]


def _dados(painel):
    a = painel["assinatura"]
    return {
        "bloqueado": painel["bloqueado"], "cobranca_configurada": painel["configurado"],
        "planos": [{"slug": p.slug, "nome": p.nome, "valor": p.valor, "intervalo": p.intervalo} for p in painel["planos"]],
        "assinatura": None if a is None else {
            "plano": a.plano.slug, "status": a.status, "forma_pagamento": a.forma_pagamento, "vigente_ate": a.vigente_ate,
            "proxima_cobranca_em": a.proxima_cobranca_em, "cancelada_em": a.cancelada_em,
        },
        "cobrancas": [{"codigo": c.pagarme_invoice_id, "valor": c.valor, "forma_pagamento": c.forma_pagamento, "status": c.status,
                       "vencimento": c.vencimento, "pago_em": c.pago_em, "url_pagamento": c.url_pagamento} for c in painel["cobrancas"]],
    }


class _Base(APIView):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "assinatura"


@extend_schema(tags=TAG, responses={200: OpenApiTypes.OBJECT})
class AssinaturaView(_Base):
    """Plano, situação, planos disponíveis e histórico de cobranças da assinatura do tenant."""

    permissoes = {"get": "assinatura.view_assinatura"}

    def get(self, request):
        return resposta(_dados(services.painel_do_cliente(request.user)))


@extend_schema(tags=TAG)
class AssinarView(_Base):
    """Assina um plano. Cartão: envie `card_token` gerado no app pelo endpoint de tokens do Pagar.me com a chave PÚBLICA
    (nunca envie número de cartão a este servidor). Boleto: sem token. Erros de regra => 422; indisponível => 503."""

    permissoes = {"post": "assinatura.gerenciar_assinatura"}
    throttle_scope = "cadastro"

    @extend_schema(request=inline_serializer("AssinarEntrada", {
        "plano": serializers.ChoiceField(choices=["mensal", "anual"]), "forma": serializers.ChoiceField(choices=[f for f, _ in Assinatura.FORMA_CHOICES]),
        "card_token": serializers.CharField(required=False, help_text="token_... (só para cartão)")}), responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        d = request.data
        try:
            a = services.assinar(request, d.get("plano", ""), d.get("forma", ""), d.get("card_token", ""))
        except pagarme.NaoConfigurado:
            return resposta(None, "Cobrança online indisponível neste ambiente.", 503)
        except pagarme.ErroPagarme as erro:
            raise RegraNegocioError(str(erro))
        return resposta({"status": a.status, "plano": a.plano.slug}, "Assinatura criada. Aguardando o primeiro pagamento.", 201)


@extend_schema(tags=TAG, request=None, responses={200: OpenApiTypes.OBJECT})
class CancelarView(_Base):
    """Cancela a assinatura (o acesso continua até o fim do período pago)."""

    permissoes = {"post": "assinatura.gerenciar_assinatura"}

    def post(self, request):
        try:
            a = services.cancelar(request)
        except pagarme.NaoConfigurado:
            return resposta(None, "Cobrança online indisponível neste ambiente.", 503)
        except pagarme.ErroPagarme as erro:
            raise RegraNegocioError(str(erro))
        return resposta({"status": a.status}, "Assinatura cancelada.")
