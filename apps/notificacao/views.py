import json
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import ListView

from webpush.models import PushInformation, SubscriptionInfo

from apps.modulo.models import Segmento
from web_project import TemplateLayout
from web_project.acesso import clientes_acessiveis, usuarios_acessiveis

from .forms import NotificacaoForm
from .models import DestinatarioNotificacao
from .services import (
    enviar_notificacao,
    marcar_como_lida,
    marcar_todas_como_lidas,
    resolver_destinatarios,
    resumo_do_usuario,
)

POR_PAGINA = 20

# O servidor FAZ requisições HTTP para o endpoint guardado na inscrição de push.
# Aceitar qualquer URL permitiria que um usuário apontasse para um serviço interno
# (SSRF); por isso só são aceitos os serviços de push dos navegadores.
PROVEDORES_PUSH = ("googleapis.com", "mozilla.com", "windows.com", "apple.com")


def _endpoint_permitido(endpoint):
    partes = urlparse(endpoint)
    host = partes.hostname or ""
    return partes.scheme == "https" and any(host == p or host.endswith("." + p) for p in PROVEDORES_PUSH)


def _data(valor):
    """Data da URL (AAAA-MM-DD) ou None: datas inválidas são ignoradas, nunca viram erro 500."""
    try:
        return parse_date(valor or "")
    except ValueError:
        return None


class NovaNotificacaoView(View):
    """Criar notificação em duas etapas: formulário -> confirmação (com a
    quantidade estimada de destinatários) -> envio."""

    def get(self, request):
        return self._render(request, NotificacaoForm(solicitante=request.user))

    def post(self, request):
        form = NotificacaoForm(request.POST, solicitante=request.user)
        if not form.is_valid():
            return self._render(request, form)
        dados = form.cleaned_data
        if not request.POST.get("confirmado"):
            total = resolver_destinatarios(request.user, dados["tipo_destinatario"], dados["segmentos"], dados["usuarios"]).count()
            return self._render(request, form, confirmacao={"total": total})
        notificacao, entregues, falhas = enviar_notificacao(request, dados)
        messages.success(
            request,
            f"Notificação enviada para {entregues + falhas} usuário(s): {entregues} entregue(s) por push, "
            f"{falhas} apenas na caixa de notificações do sistema.",
        )
        return redirect("notificacao-list")

    def _render(self, request, form, confirmacao=None):
        context = TemplateLayout.init(self, {"form": form, "confirmacao": confirmacao})
        return render(request, "notificacao_form.html", context)


class HistoricoView(ListView):
    """Histórico de envios (um registro por destinatário), com filtros e paginação.
    Escopo: só destinatários da estrutura do solicitante (Admin: todos)."""

    template_name = "notificacao_historico.html"
    context_object_name = "itens"
    paginate_by = POR_PAGINA

    SITUACOES = {
        "entregues": Q(status_entrega=DestinatarioNotificacao.ENTREGA_ENTREGUE),
        "nao-entregues": ~Q(status_entrega=DestinatarioNotificacao.ENTREGA_ENTREGUE),
        "lidas": Q(lida_em__isnull=False),
        "nao-lidas": Q(lida_em__isnull=True),
    }

    def get_queryset(self):
        params = self.request.GET
        queryset = DestinatarioNotificacao.objects.filter(
            usuario__in=usuarios_acessiveis(self.request.user)
        ).select_related("notificacao", "usuario", "usuario__profile__cliente__segmento")
        if params.get("situacao") in self.SITUACOES:
            queryset = queryset.filter(self.SITUACOES[params["situacao"]])
        if params.get("usuario", "").isdigit():
            queryset = queryset.filter(usuario_id=params["usuario"])
        if params.get("cliente", "").isdigit():
            queryset = queryset.filter(usuario__profile__cliente_id=params["cliente"])
        if params.get("segmento", "").isdigit():
            queryset = queryset.filter(usuario__profile__cliente__segmento_id=params["segmento"])
        if params.get("entrega") in dict(DestinatarioNotificacao.ENTREGA_CHOICES):
            queryset = queryset.filter(status_entrega=params["entrega"])
        if params.get("leitura") == "lida":
            queryset = queryset.filter(lida_em__isnull=False)
        elif params.get("leitura") == "nao-lida":
            queryset = queryset.filter(lida_em__isnull=True)
        if _data(params.get("de")):
            queryset = queryset.filter(criado_em__date__gte=_data(params["de"]))
        if _data(params.get("ate")):
            queryset = queryset.filter(criado_em__date__lte=_data(params["ate"]))
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(notificacao__titulo__icontains=termo) | Q(notificacao__mensagem__icontains=termo))
        return queryset

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        solicitante = self.request.user
        consulta = self.request.GET.copy()
        consulta.pop("page", None)
        context.update(
            filtros=self.request.GET,
            querystring=consulta.urlencode(),
            usuarios=usuarios_acessiveis(solicitante).order_by("username"),
            clientes=clientes_acessiveis(solicitante).order_by("nome_razao_social"),
            segmentos=Segmento.objects.filter(clientes__in=clientes_acessiveis(solicitante)).distinct(),
            entregas=DestinatarioNotificacao.ENTREGA_CHOICES,
        )
        return context


@method_decorator(login_required, name="dispatch")
class MinhasNotificacoesView(ListView):
    """"Ver todas as notificações": a caixa de entrada do próprio usuário."""

    template_name = "notificacao_minhas.html"
    context_object_name = "itens"
    paginate_by = POR_PAGINA

    def get_queryset(self):
        queryset = DestinatarioNotificacao.objects.filter(usuario=self.request.user).select_related("notificacao")
        situacao = self.request.GET.get("situacao")
        if situacao == "nao-lidas":
            queryset = queryset.filter(lida_em__isnull=True)
        elif situacao == "lidas":
            queryset = queryset.filter(lida_em__isnull=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["situacao"] = self.request.GET.get("situacao", "")
        return context

    def post(self, request):
        marcar_todas_como_lidas(request.user)
        return redirect("notificacao-minhas")


@method_decorator(login_required, name="dispatch")
class AbrirNotificacaoView(View):
    """Marca como lida (com data/hora) e segue para o link da notificação.
    Só funciona com notificações do PRÓPRIO usuário (outro id vira 404)."""

    def get(self, request, pk):
        item = get_object_or_404(DestinatarioNotificacao.objects.select_related("notificacao"), pk=pk, usuario=request.user)
        marcar_como_lida(item)
        destino = item.notificacao.link
        if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts=None):
            return redirect(destino)
        return redirect("notificacao-minhas")


@method_decorator([never_cache, login_required], name="dispatch")
class ResumoNotificacoesView(View):
    """Atualização automática do sino: devolve o contador e a lista renderizada
    do PRÓPRIO usuário (a página consulta periodicamente). Não aceita parâmetros
    de usuário, então não há como ler a caixa de outra pessoa."""

    def get(self, request):
        nao_lidas, recentes = resumo_do_usuario(request.user)
        html = render_to_string(
            "layout/partials/navbar/notificacoes_itens.html", {"notificacoes_recentes": recentes}, request=request
        )
        return JsonResponse({"nao_lidas": nao_lidas, "html": html})


@method_decorator(login_required, name="dispatch")
class InscricaoPushView(View):
    """Guarda a inscrição de push do navegador do usuário logado (django-webpush).
    Substitui a view padrão do pacote, que é isenta de CSRF e aceita anônimos."""

    def post(self, request):
        try:
            dados = json.loads(request.body.decode("utf-8"))
            assinatura = dados["subscription"]
            chaves = assinatura["keys"]
            endpoint, auth, p256dh = assinatura["endpoint"], chaves["auth"], chaves["p256dh"]
        except (ValueError, KeyError, TypeError):
            return JsonResponse({"erro": "Inscrição inválida."}, status=400)
        if not (isinstance(endpoint, str) and _endpoint_permitido(endpoint)):
            return JsonResponse({"erro": "Provedor de push não suportado."}, status=400)

        inscricao, _ = SubscriptionInfo.objects.get_or_create(
            endpoint=endpoint, auth=auth, p256dh=p256dh,
            defaults={"browser": str(dados.get("browser", ""))[:100], "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500]},
        )
        PushInformation.objects.get_or_create(user=request.user, subscription=inscricao)
        return JsonResponse({"ok": True}, status=201)
