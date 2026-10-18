import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_edicao, registrar_exclusao, tirar_foto
from auth import ratelimit
from auth.ratelimit import get_client_ip
from web_project import TemplateLayout
from web_project.acesso import cliente_do_usuario, clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin
from web_project.regras import RegraNegocioError

from . import services as s
from .adaptadores import disponiveis, obter
from .forms import CompromissoForm, MensagemForm, formulario_conexao, separar
from .models import Compromisso, Conexao, LogIntegracao, Mensagem

LIMITE_WEBHOOK = (300, 60)  # 300 chamadas por minuto por conexão e IP


def _conexao(request, pk, acao="view"):
    return get_object_or_404(Conexao.objects.select_related("cliente"), pk=pk, cliente__in=clientes_dos_itens(request.user, acao))


def _url_base(request):
    return (getattr(settings, "BASE_URL", "") or "").rstrip("/") or request.build_absolute_uri("/").rstrip("/")


def _pagina(view, request, template, contexto):
    return render(request, template, TemplateLayout.init(view, contexto))


class IntegracoesView(View):
    def get(self, request):
        conexoes = Conexao.objects.filter(cliente__in=clientes_dos_itens(request.user, "view")).select_related("cliente")
        if not settings.DEBUG:
            conexoes = conexoes.exclude(provedor="teste")  # o provedor simulado só existe em desenvolvimento
        return _pagina(self, request, "integracoes.html", {"conexoes": conexoes, "provedores": disponiveis(), "google_configurado": obter("google_calendar").configurado()})


class NovaConexaoView(View):
    template = "integracao_form.html"

    def _adaptador(self, provedor):
        adaptador = obter_ou_404(provedor)
        if adaptador.usa_oauth:
            raise Http404  # provedores OAuth têm fluxo próprio (autorização no provedor)
        return adaptador

    def get(self, request, provedor):
        adaptador = self._adaptador(provedor)
        return _pagina(self, request, self.template, {"adaptador": adaptador, "form": formulario_conexao(adaptador)()})

    def post(self, request, provedor):
        adaptador = self._adaptador(provedor)
        form = formulario_conexao(adaptador)(request.POST)
        if form.is_valid():
            credenciais, config = separar(adaptador, form.cleaned_data)
            try:
                conexao = s.conectar(request, request.user.profile.cliente, provedor, form.cleaned_data["nome"], credenciais, config)
                conexao.notificar_usuarios = form.cleaned_data["notificar_usuarios"]
                conexao.save(update_fields=["notificar_usuarios"])
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                if conexao.status == Conexao.CONECTADA:
                    messages.success(request, "Conexão criada e verificada.")
                else:
                    messages.warning(request, f"Conexão criada, mas o teste falhou: {conexao.ultimo_erro}")
                return redirect("integracao-detalhe", pk=conexao.pk)
        return _pagina(self, request, self.template, {"adaptador": adaptador, "form": form})


def obter_ou_404(provedor):
    try:
        adaptador = obter(provedor)
    except Exception:
        raise Http404 from None
    if adaptador not in disponiveis():
        raise Http404
    return adaptador


class DetalheView(View):
    def get(self, request, pk):
        conexao = _conexao(request, pk)
        adaptador = conexao.adaptador
        return _pagina(self, request, "integracao.html", {
            "conexao": conexao, "adaptador": adaptador, "logs": conexao.logs.all()[:50], "webhook_url": f"{_url_base(request)}{reverse('integracao-webhook', args=[conexao.webhook_token])}" if adaptador.suporta_webhook else "",
            "pode_alterar": request.user.has_perm("integracao.change_conexao"), "pode_enviar": request.user.has_perm("integracao.enviar_mensagem") and adaptador.envia_mensagens,
            "tem_mensagens": adaptador.envia_mensagens,
        })


class AcaoView(View):
    """POST: testar | sincronizar | desconectar | excluir (permissão change; excluir exige delete)."""

    def post(self, request, pk, acao):
        conexao = _conexao(request, pk, "change")
        try:
            if acao == "testar":
                messages.success(request, "Conexão verificada.") if s.testar_conexao(request, conexao) else messages.error(request, f"Falha no teste: {conexao.ultimo_erro}")
            elif acao == "sincronizar":
                if not conexao.adaptador.sincroniza_agenda:
                    raise RegraNegocioError("Esta conexão não sincroniza agenda.")
                resumo = s.sincronizar_agenda(conexao)
                messages.success(request, "Sincronização concluída: " + ", ".join(f"{k} {v}" for k, v in resumo.items() if v) or "sem alterações.")
            elif acao == "desconectar":
                s.desconectar(request, conexao, revogar=True)
                messages.success(request, "Conexão desconectada e credenciais apagadas.")
            elif acao == "excluir":
                if not request.user.has_perm("integracao.delete_conexao"):
                    raise PermissionDenied
                s.excluir_conexao(request, conexao)
                messages.success(request, "Conexão excluída.")
                return redirect("integracoes")
            else:
                raise Http404
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("integracao-detalhe", pk=conexao.pk)


class CredenciaisView(View):
    """Substitui credenciais (nunca mostra as atuais; campo vazio mantém)."""

    def get(self, request, pk):
        conexao = _conexao(request, pk, "change")
        if conexao.adaptador.usa_oauth:
            raise Http404
        return _pagina(self, request, "integracao_form.html", {"adaptador": conexao.adaptador, "conexao": conexao, "form": formulario_conexao(conexao.adaptador, editando=True)(initial={"nome": conexao.nome})})

    def post(self, request, pk):
        conexao = _conexao(request, pk, "change")
        adaptador = conexao.adaptador
        if adaptador.usa_oauth:
            raise Http404
        form = formulario_conexao(adaptador, editando=True)(request.POST)
        if form.is_valid():
            credenciais, config = separar(adaptador, form.cleaned_data)
            try:
                ok = s.atualizar_credenciais(request, conexao, credenciais, config)
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                (messages.success if ok else messages.warning)(request, "Credenciais atualizadas e verificadas." if ok else "Credenciais salvas, mas o teste falhou.")
                return redirect("integracao-detalhe", pk=conexao.pk)
        return _pagina(self, request, "integracao_form.html", {"adaptador": adaptador, "conexao": conexao, "form": form})


# --------------------------------------------------------------------------
# Google (OAuth2)
# --------------------------------------------------------------------------

SAL_OAUTH = "integracao-google"


class GoogleIniciarView(View):
    def post(self, request):
        adaptador = obter("google_calendar")
        if not adaptador.configurado():
            messages.error(request, "O Google Agenda não está configurado neste sistema (faltam GOOGLE_OAUTH_CLIENT_ID e GOOGLE_OAUTH_CLIENT_SECRET no .env).")
            return redirect("integracoes")
        nonce = secrets.token_urlsafe(16)
        request.session["integracao_oauth_nonce"] = nonce
        estado = signing.dumps({"u": request.user.pk, "n": nonce}, salt=SAL_OAUTH)
        return redirect(adaptador.url_autorizacao(estado, _url_base(request) + reverse("integracao-google-retorno")))


class GoogleRetornoView(View):
    """Destino do redirecionamento do Google: confere o `state` (assinado, de uso único, do mesmo usuário), troca o código por tokens
    (cifrados) e cria a conexão. Qualquer divergência cancela o fluxo."""

    def get(self, request):
        adaptador = obter("google_calendar")
        nonce = request.session.pop("integracao_oauth_nonce", None)
        try:
            estado = signing.loads(request.GET.get("state", ""), salt=SAL_OAUTH, max_age=600)
        except signing.BadSignature:
            estado = None
        if not estado or not nonce or estado.get("n") != nonce or estado.get("u") != request.user.pk:
            messages.error(request, "Autorização inválida ou expirada. Tente conectar de novo.")
            return redirect("integracoes")
        if request.GET.get("error") or not request.GET.get("code"):
            messages.warning(request, "A autorização foi negada ou cancelada no Google.")
            return redirect("integracoes")
        try:
            credenciais, expira = adaptador.trocar_codigo(request.GET["code"], _url_base(request) + reverse("integracao-google-retorno"))
            conexao = s.conectar(request, request.user.profile.cliente, "google_calendar", "Google Agenda", credenciais, {"calendar_id": "primary", "incluir_valores": "0"}, testar=True)
            conexao.expira_em = expira
            conexao.save(update_fields=["expira_em"])
        except (RegraNegocioError, Exception) as erro:  # noqa: BLE001 (nunca vaza detalhe do provedor)
            messages.error(request, f"Não foi possível concluir a conexão: {erro if isinstance(erro, RegraNegocioError) else 'erro ao falar com o Google.'}")
            return redirect("integracoes")
        messages.success(request, "Google Agenda conectado.")
        return redirect("integracao-detalhe", pk=conexao.pk)


# --------------------------------------------------------------------------
# Mensagens
# --------------------------------------------------------------------------

class MensagensView(View):
    def get(self, request, pk):
        conexao = _conexao(request, pk)
        if not conexao.adaptador.envia_mensagens:
            raise Http404
        direcao = request.GET.get("direcao") if request.GET.get("direcao") in ("saida", "entrada") else ""
        consulta = conexao.mensagens.all()
        if direcao:
            consulta = consulta.filter(direcao=direcao)
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return _pagina(self, request, "integracao_mensagens.html", {"conexao": conexao, "mensagens": pagina, "page_obj": pagina, "direcao": direcao, "form": MensagemForm(), "pode_enviar": request.user.has_perm("integracao.enviar_mensagem")})

    def post(self, request, pk):
        conexao = _conexao(request, pk)
        if not request.user.has_perm("integracao.enviar_mensagem"):
            raise PermissionDenied
        form = MensagemForm(request.POST)
        if form.is_valid():
            try:
                s.enfileirar_mensagem(request, conexao, form.cleaned_data["contato"], form.cleaned_data["texto"], chave=request.POST.get("chave") or None)
                messages.success(request, "Mensagem na fila de envio.")
            except RegraNegocioError as erro:
                messages.error(request, str(erro))
        else:
            messages.error(request, "Confira o contato e o texto.")
        return redirect("integracao-mensagens", pk=conexao.pk)


# --------------------------------------------------------------------------
# Webhook (público: autenticado pela ASSINATURA do provedor)
# --------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    def _conexao(self, token):
        return Conexao.objects.select_related("cliente").filter(webhook_token=token).first()

    def _limitado(self, request, conexao):
        identificador = f"{conexao.pk}:{get_client_ip(request)}"
        if ratelimit.check_rate_limit("webhook", identificador, *LIMITE_WEBHOOK).blocked:
            return True
        ratelimit.hit("webhook", identificador, LIMITE_WEBHOOK[1])
        return False

    def get(self, request, token):
        """Desafio de verificação do provedor (ex.: Meta envia hub.challenge)."""
        conexao = self._conexao(token)
        if conexao is None or conexao.status != Conexao.CONECTADA or self._limitado(request, conexao):
            raise Http404
        try:
            resposta = conexao.adaptador.desafio_webhook(conexao, s.credenciais_de(conexao), request.GET)
        except Exception:  # noqa: BLE001
            resposta = None
        if resposta is None:
            return HttpResponseForbidden("verificação negada")
        return HttpResponse(str(resposta), content_type="text/plain")

    def post(self, request, token):
        conexao = self._conexao(token)
        if conexao is None:
            raise Http404
        if self._limitado(request, conexao):
            return HttpResponse("muitas requisições", status=429)
        status, texto = s.tratar_webhook(conexao, request.headers, request.body)
        return HttpResponse(texto, status=status, content_type="text/plain")


# --------------------------------------------------------------------------
# Agenda (compromissos)
# --------------------------------------------------------------------------

class CompromissoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    model = Compromisso
    template_name = "compromisso.html"
    context_object_name = "compromissos"
    colunas_ordenaveis = {"inicio": "inicio", "titulo": "titulo"}
    ordenacao_padrao = "inicio"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(titulo__icontains=termo)
        tipo = self.request.GET.get("tipo")
        if tipo in dict(Compromisso.TIPO_CHOICES):
            queryset = queryset.filter(tipo=tipo)
        return self.ordenar(self.filtrar_status(queryset))

    def get(self, request, *args, **kwargs):
        # Abrir a Agenda sincroniza antes de listar (no máximo a cada 5 min por conexão; falhas não impedem a tela).
        cliente = cliente_do_usuario(request.user)
        if cliente is not None:
            s.sincronizar_automatico(cliente)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["tipos"] = Compromisso.TIPO_CHOICES
        context["visao"] = "calendario" if self.request.GET.get("visao") == "calendario" else "tabela"
        context["tipo"] = self.request.GET.get("tipo", "")
        return context


def _compromissos_do_usuario(user, acao):
    """Compromissos que o usuário alcança para a ação ('view' ou 'change'): mesmo escopo de unidades da tela."""
    return Compromisso.objects.filter(cliente__in=clientes_dos_itens(user, acao))


class CompromissoEventosView(View):
    """JSON para o calendário (FullCalendar): compromissos ativos entre `start` e `end` (ISO). Só o escopo do usuário."""

    def get(self, request):
        inicio, fim = parse_datetime(request.GET.get("start", "")), parse_datetime(request.GET.get("end", ""))
        queryset = _compromissos_do_usuario(request.user, "view").filter(ativo=True)
        if inicio and fim:
            queryset = queryset.filter(inicio__lt=fim, fim__gte=inicio)
        pode_mover = request.user.has_perm("integracao.change_compromisso")
        editaveis = set(_compromissos_do_usuario(request.user, "change").values_list("pk", flat=True)) if pode_mover else set()
        eventos = [
            {
                "id": c.pk, "title": c.titulo, "start": c.inicio.isoformat(), "end": c.fim.isoformat(), "allDay": c.todo_o_dia,
                "url": reverse("compromisso-edit", args=[c.pk]) if c.pk in editaveis else "", "editable": c.pk in editaveis,
                "classNames": [f"tipo-{c.tipo}"] + (["concluido"] if c.concluido else []),
                "extendedProps": {"tipo": c.get_tipo_display(), "concluido": c.concluido, "origem": c.origem},
            }
            for c in queryset.order_by("inicio")[:1000]
        ]
        return JsonResponse(eventos, safe=False)


class CompromissoMoverView(View):
    """POST JSON {inicio, fim?, todo_o_dia}: arrastar e soltar no calendário. Mantém a duração se `fim` não vier."""

    def post(self, request, pk):
        compromisso = get_object_or_404(_compromissos_do_usuario(request.user, "change"), pk=pk)
        try:
            dados = json.loads(request.body or b"{}")
            novo_inicio = parse_datetime(str(dados.get("inicio", "")))
            novo_fim = parse_datetime(str(dados.get("fim") or "")) if dados.get("fim") else None
        except ValueError:
            return JsonResponse({"erro": "Dados inválidos."}, status=400)
        if novo_inicio is None:
            return JsonResponse({"erro": "Data inválida."}, status=400)
        if timezone.is_naive(novo_inicio):
            novo_inicio = timezone.make_aware(novo_inicio)
        if novo_fim is None:
            novo_fim = novo_inicio + (compromisso.fim - compromisso.inicio)
        elif timezone.is_naive(novo_fim):
            novo_fim = timezone.make_aware(novo_fim)
        if novo_fim < novo_inicio:
            return JsonResponse({"erro": "O fim não pode ser anterior ao início."}, status=400)
        antes = tirar_foto(compromisso)
        with transaction.atomic():
            compromisso.inicio, compromisso.fim = novo_inicio, novo_fim
            if "todo_o_dia" in dados:
                compromisso.todo_o_dia = bool(dados["todo_o_dia"])
            compromisso.save()
            registrar_edicao(request, compromisso, antes, modulo="integracao")
        return JsonResponse({"ok": True})


class CompromissoConcluirView(View):
    """POST: alterna concluído/reaberto."""

    def post(self, request, pk):
        compromisso = get_object_or_404(_compromissos_do_usuario(request.user, "change"), pk=pk)
        antes = tirar_foto(compromisso)
        with transaction.atomic():
            compromisso.concluido = not compromisso.concluido
            compromisso.save(update_fields=["concluido", "atualizado_em"])
            registrar_edicao(request, compromisso, antes, modulo="integracao")
        messages.success(request, "Marcado como concluído." if compromisso.concluido else "Reaberto.")
        return redirect(request.POST.get("next") or "compromisso-list")


class CompromissoExcluirView(View):
    """POST: exclui da agenda (exclusão LÓGICA: o registro fica inativo e a sincronização remove o evento externo)."""

    def post(self, request, pk):
        compromisso = get_object_or_404(_compromissos_do_usuario(request.user, "change"), pk=pk)
        with transaction.atomic():
            registrar_exclusao(request, compromisso, logica=True, modulo="integracao")
            compromisso.ativo = False
            compromisso.save(update_fields=["ativo", "atualizado_em"])
        messages.success(request, "Registro excluído da agenda.")
        return redirect(request.POST.get("next") or "compromisso-list")


class _CompromissoForm:
    model, form_class, template_name = Compromisso, CompromissoForm, "compromisso_form.html"
    success_url = reverse_lazy("compromisso-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class CompromissoCreateView(_CompromissoForm, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo, nome_url_novo = "add", "compromisso-create"

    def form_valid(self, form):
        messages.success(self.request, "Compromisso salvo. Ele entra nas agendas conectadas na próxima sincronização.")
        return super().form_valid(form)


class CompromissoUpdateView(_CompromissoForm, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Compromisso atualizado.")
        return super().form_valid(form)
