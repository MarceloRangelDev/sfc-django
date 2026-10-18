"""Telas da assinatura e o webhook público do Pagar.me."""
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from auth import ratelimit
from auth.ratelimit import get_client_ip
from web_project import TemplateLayout
from web_project.regras import RegraNegocioError

from . import pagarme, services

LIMITE_WEBHOOK = (600, 60)  # 600 chamadas por minuto por IP (picos de reenvio do provedor)
TAMANHO_MAXIMO = 64 * 1024  # corpo máximo do webhook


class AssinaturaView(TemplateView):
    """Página "Assinatura" do tenant do usuário (plano, status, cobranças, linha do tempo e ações)."""

    template_name = "assinatura.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(services.painel_do_cliente(self.request.user))
        return context


class AssinarView(View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            services.assinar(request, request.POST.get("plano", ""), request.POST.get("forma", ""), request.POST.get("card_token", ""))
        except (RegraNegocioError, pagarme.ErroPagarme) as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Assinatura criada. Aguardando a confirmação do primeiro pagamento.")
        return redirect("assinatura")


class CancelarView(View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            services.cancelar(request)
        except (RegraNegocioError, pagarme.ErroPagarme) as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Assinatura cancelada. O acesso continua até o fim do período pago.")
        return redirect("assinatura")


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """Endpoint PÚBLICO chamado pelo Pagar.me (POST JSON). Autenticação: segredo longo na URL (`PAGARME_WEBHOOK_TOKEN`,
    comparado em tempo constante) + confirmação do pagamento direto na API do provedor (ver services). Sem token configurado
    ou token errado => 404 (não revela que a rota existe). Corpo limitado; resposta rápida e sem eco de dados.
    Respostas: 200 processado/duplicado/ignorado; 400 payload inválido; 503 falha transitória (o provedor reenvia)."""

    http_method_names = ["post"]

    def post(self, request, token):
        esperado = settings.PAGARME_WEBHOOK_TOKEN
        if not esperado or not hmac.compare_digest(token.encode(), esperado.encode()):
            raise Http404
        ip = get_client_ip(request)
        if ratelimit.check_rate_limit("pagarme_webhook", ip, *LIMITE_WEBHOOK).blocked:
            return HttpResponse("muitas requisições", status=429)
        ratelimit.hit("pagarme_webhook", ip, LIMITE_WEBHOOK[1])
        if len(request.body) > TAMANHO_MAXIMO:
            return HttpResponse("corpo grande demais", status=413)
        try:
            resultado = services.processar_webhook(json.loads(request.body))
        except (ValueError, UnicodeDecodeError):
            return HttpResponse("payload inválido", status=400)
        except pagarme.ErroPagarme:
            return HttpResponse("tente novamente", status=503)
        return HttpResponse(resultado, content_type="text/plain")
