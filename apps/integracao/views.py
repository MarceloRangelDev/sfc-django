import json
import re
import secrets
from datetime import datetime, time, timedelta

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
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_criacao, registrar_edicao, registrar_exclusao, tirar_foto
from auth import ratelimit
from auth.ratelimit import get_client_ip
from web_project import TemplateLayout
from web_project.acesso import cliente_do_usuario, clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin
from web_project.regras import RegraNegocioError

from . import services as s
from . import templates_whatsapp as tw
from .adaptadores import disponiveis, obter
from .forms import BOTOES_NA_TELA, CompromissoForm, MensagemForm, TemplateWhatsAppForm, formulario_conexao, separar
from .models import Compromisso, Conexao, LogIntegracao, Mensagem, TemplateWhatsApp

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


PROVEDORES_GOOGLE = {"google_calendar": ("Google Agenda", {"calendar_id": "primary", "incluir_valores": "0"}), "google_tasks": ("Google Tarefas", {"tasklist": "@default"})}


class GoogleIniciarView(View):
    def post(self, request):
        provedor = request.POST.get("provedor", "google_calendar")
        if provedor not in PROVEDORES_GOOGLE:  # lista fechada: nunca aceita provedor arbitrário do navegador
            raise Http404
        adaptador = obter(provedor)
        if not adaptador.configurado():
            messages.error(request, "O Google não está configurado neste sistema (faltam GOOGLE_OAUTH_CLIENT_ID e GOOGLE_OAUTH_CLIENT_SECRET no .env).")
            return redirect("integracoes")
        nonce = secrets.token_urlsafe(16)
        request.session["integracao_oauth_nonce"] = nonce
        estado = signing.dumps({"u": request.user.pk, "n": nonce, "p": provedor}, salt=SAL_OAUTH)
        return redirect(adaptador.url_autorizacao(estado, _url_base(request) + reverse("integracao-google-retorno")))


class GoogleRetornoView(View):
    """Destino do redirecionamento do Google: confere o `state` (assinado, de uso único, do mesmo usuário), troca o código por tokens
    (cifrados) e cria a conexão. Qualquer divergência cancela o fluxo."""

    def get(self, request):
        nonce = request.session.pop("integracao_oauth_nonce", None)
        try:
            estado = signing.loads(request.GET.get("state", ""), salt=SAL_OAUTH, max_age=600)
        except signing.BadSignature:
            estado = None
        provedor = (estado or {}).get("p", "google_calendar")
        if not estado or not nonce or estado.get("n") != nonce or estado.get("u") != request.user.pk or provedor not in PROVEDORES_GOOGLE:
            messages.error(request, "Autorização inválida ou expirada. Tente conectar de novo.")
            return redirect("integracoes")
        if request.GET.get("error") or not request.GET.get("code"):
            messages.warning(request, "A autorização foi negada ou cancelada no Google.")
            return redirect("integracoes")
        adaptador = obter(provedor)
        rotulo, config = PROVEDORES_GOOGLE[provedor]
        try:
            credenciais, expira = adaptador.trocar_codigo(request.GET["code"], _url_base(request) + reverse("integracao-google-retorno"))
            conexao = s.conectar(request, request.user.profile.cliente, provedor, rotulo, credenciais, dict(config), testar=True)
            conexao.expira_em = expira
            conexao.save(update_fields=["expira_em"])
        except (RegraNegocioError, Exception) as erro:  # noqa: BLE001 (nunca vaza detalhe do provedor)
            messages.error(request, f"Não foi possível concluir a conexão: {erro if isinstance(erro, RegraNegocioError) else 'erro ao falar com o Google.'}")
            return redirect("integracoes")
        messages.success(request, f"{rotulo} conectado.")
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
        # Situação: em aberto / atrasados / concluídos (convive com o filtro de status ativo/inativo).
        queryset = s.filtrar_por_situacao(queryset, self.request.GET.get("situacao"))
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
        context["situacao"] = self.request.GET.get("situacao", "") if self.request.GET.get("situacao") in s.SITUACOES_DA_AGENDA else ""
        # Quem gere integrações vê, na Agenda, as conexões de calendário com problema (mensagem já sem segredos); os demais não.
        if self.request.user.has_perm("integracao.view_conexao"):
            context["conexoes_com_erro"] = [
                c for c in Conexao.objects.filter(cliente__in=clientes_dos_itens(self.request.user, "view"), status=Conexao.ERRO) if c.adaptador.sincroniza_agenda
            ]
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
        queryset = s.filtrar_por_situacao(queryset, request.GET.get("situacao"))
        pode_mover = request.user.has_perm("integracao.change_compromisso")
        editaveis = set(_compromissos_do_usuario(request.user, "change").values_list("pk", flat=True)) if pode_mover else set()
        eventos = []
        for c in queryset.order_by("inicio")[:1000]:
            if c.todo_o_dia:
                # Dia inteiro: o calendário recebe DATAS (fim exclusivo = dia seguinte). Mandar o instante UTC faria o importado
                # (guardado 00:00 UTC) aparecer no dia anterior no fuso do Brasil.
                inicio_cal, fim_cal = c.data_inicial.isoformat(), (c.data_final + timedelta(days=1)).isoformat()
                inicio_form, fim_form = f"{c.data_inicial.isoformat()}T00:00", f"{c.data_final.isoformat()}T23:59"
            else:
                inicio_cal, fim_cal = c.inicio.isoformat(), c.fim.isoformat()
                inicio_form, fim_form = timezone.localtime(c.inicio).strftime("%Y-%m-%dT%H:%M"), timezone.localtime(c.fim).strftime("%Y-%m-%dT%H:%M")
            eventos.append({
                "id": c.pk, "title": c.titulo, "start": inicio_cal, "end": fim_cal, "allDay": c.todo_o_dia,
                "editable": c.pk in editaveis,
                "classNames": [f"tipo-{c.tipo}"] + (["concluido"] if c.concluido else []),
                "extendedProps": {
                    "tipo": c.get_tipo_display(), "concluido": c.concluido, "origem": c.origem,
                    # Dados para o modal de edição (horário local, formato do <input datetime-local>).
                    "form": {
                        "tipo": c.tipo, "titulo": c.titulo, "descricao": c.descricao, "todo_o_dia": c.todo_o_dia, "inicio": inicio_form, "fim": fim_form,
                        "lembrete_minutos": "" if c.lembrete_minutos is None else str(c.lembrete_minutos),
                    },
                },
            })
        return JsonResponse(eventos, safe=False)


def _completar_com_atuais(post, instancia):
    """Dados do modal + o que ele não enviou. O modal não tem os campos `ativo` e `concluido`; como o formulário trata campo ausente
    como "vazio/desmarcado", sem isto criar nasceria INATIVO (e sumiria do calendário) e editar apagaria `concluido`/`ativo` e qualquer
    campo omitido. Regra: criar => ativo; editar => só muda o que veio no POST (chave presente, mesmo vazia, é decisão do usuário)."""
    dados = post.copy()
    if instancia is None:
        dados.setdefault("ativo", "on")
        return dados
    atuais = {
        "tipo": instancia.tipo, "titulo": instancia.titulo, "descricao": instancia.descricao,
        "inicio": timezone.localtime(instancia.inicio).strftime("%Y-%m-%dT%H:%M"), "fim": timezone.localtime(instancia.fim).strftime("%Y-%m-%dT%H:%M"),
        "lembrete_minutos": "" if instancia.lembrete_minutos is None else str(instancia.lembrete_minutos),
    }
    for campo, valor in atuais.items():
        dados.setdefault(campo, valor)
    for campo in ("todo_o_dia", "concluido", "ativo"):  # caixas: ausente = desmarcada; só as verdadeiras precisam ser repostas
        if campo not in dados and getattr(instancia, campo):
            dados[campo] = "on"
    return dados


class CompromissoSalvarView(View):
    """POST (form-encoded, CSRF) usado pelo modal do calendário: cria (sem `pk`) ou edita, com o MESMO CompromissoForm
    da tela completa (validação, auditoria e escopo de unidade iguais). Devolve {ok} ou {erros: {campo: [msgs]}} (400).
    A permissão (add/change) é exigida na rota; o dono do novo compromisso é sempre a unidade do usuário autenticado."""

    def post(self, request, pk=None):
        if pk is None:
            instancia, cliente = None, cliente_do_usuario(request.user)
            if cliente is None:
                return JsonResponse({"erros": {"__all__": ["Seu usuário não está vinculado a uma unidade."]}}, status=400)
        else:
            instancia = get_object_or_404(_compromissos_do_usuario(request.user, "change"), pk=pk)
            cliente = instancia.cliente
        antes = tirar_foto(instancia) if instancia else None
        form = CompromissoForm(_completar_com_atuais(request.POST, instancia), instance=instancia, cliente=cliente)
        if not form.is_valid():
            return JsonResponse({"erros": {k: [str(e) for e in v] for k, v in form.errors.items()}}, status=400)
        with transaction.atomic():
            compromisso = form.save(commit=False)
            if instancia is None:
                compromisso.cliente = cliente
            compromisso.save()
            if instancia is None:
                registrar_criacao(request, compromisso, modulo="integracao")
            else:
                registrar_edicao(request, compromisso, antes, modulo="integracao")
        return JsonResponse({"ok": True, "id": compromisso.pk})


def _so_data(texto):
    """True para 'AAAA-MM-DD' (o Python 3.11+ também lê isso como data-hora à meia-noite, então a forma do texto é que decide)."""
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(texto or "")) is not None


def _ler_momento(texto):
    """'2026-10-21T09:00:00-03:00' ou '2026-10-21' (dia inteiro) -> (datetime aware, veio_so_data). Vazio/ilegível -> (None, False)."""
    texto = str(texto or "")
    if _so_data(texto):
        dia = parse_date(texto)
        return (timezone.make_aware(datetime.combine(dia, time.min)), True) if dia is not None else (None, False)
    momento = parse_datetime(texto)
    if momento is not None:
        return (timezone.make_aware(momento) if timezone.is_naive(momento) else momento), False
    return None, False


class CompromissoMoverView(View):
    """POST JSON {inicio, fim?, todo_o_dia}: arrastar e soltar no calendário. Mantém a duração se `fim` não vier."""

    def post(self, request, pk):
        compromisso = get_object_or_404(_compromissos_do_usuario(request.user, "change"), pk=pk)
        try:
            dados = json.loads(request.body or b"{}")
            novo_inicio, so_data_inicio = _ler_momento(dados.get("inicio"))
            novo_fim, _ = _ler_momento(dados.get("fim")) if dados.get("fim") else (None, False)
        except (ValueError, TypeError, AttributeError):
            return JsonResponse({"erro": "Dados inválidos."}, status=400)
        if novo_inicio is None:
            return JsonResponse({"erro": "Data inválida."}, status=400)
        era_dia_inteiro = compromisso.todo_o_dia
        dia_inteiro = bool(dados["todo_o_dia"]) if "todo_o_dia" in dados else (era_dia_inteiro or so_data_inicio)
        if dia_inteiro:
            # Só as datas importam: mantém a quantidade de dias (ou usa o fim informado, que no calendário é exclusivo).
            primeiro = timezone.localtime(novo_inicio).date()
            if novo_fim is not None and _so_data(dados.get("fim")):
                ultimo = max(primeiro, timezone.localtime(novo_fim).date() - timedelta(days=1))
            elif era_dia_inteiro:
                ultimo = primeiro + (compromisso.data_final - compromisso.data_inicial)
            else:
                ultimo = primeiro
            fuso = timezone.get_current_timezone()
            novo_inicio = timezone.make_aware(datetime.combine(primeiro, time.min), fuso)
            novo_fim = timezone.make_aware(datetime.combine(ultimo, time(23, 59)), fuso)
        elif novo_fim is None:
            # De "dia inteiro" para horário sem fim informado: 1 hora; senão mantém a duração.
            novo_fim = novo_inicio + (timedelta(hours=1) if era_dia_inteiro else compromisso.fim - compromisso.inicio)
        if novo_fim < novo_inicio:
            return JsonResponse({"erro": "O fim não pode ser anterior ao início."}, status=400)
        antes = tirar_foto(compromisso)
        with transaction.atomic():
            compromisso.inicio, compromisso.fim = novo_inicio, novo_fim
            compromisso.lembrete_enviado_em = None  # novo horário: o lembrete vale de novo
            compromisso.todo_o_dia = dia_inteiro
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


# --------------------------------------------------------------------------
# Templates do WhatsApp Business (API oficial da Meta)
# --------------------------------------------------------------------------

def _conexoes_templates(request, acao="view"):
    """Conexões do WhatsApp oficial da unidade (e filiais autorizadas) do usuário: o ÚNICO ponto de onde a tela escolhe uma conexão."""
    return Conexao.objects.filter(provedor="whatsapp_cloud", cliente__in=clientes_dos_itens(request.user, acao)).select_related("cliente")


def _template(request, pk, acao="view"):
    return get_object_or_404(TemplateWhatsApp.objects.select_related("conexao", "conexao__cliente"), pk=pk, conexao__cliente__in=clientes_dos_itens(request.user, acao))


class TemplateListView(View):
    def get(self, request):
        conexoes = _conexoes_templates(request)
        consulta = TemplateWhatsApp.objects.filter(conexao__in=conexoes).select_related("conexao")
        filtros = {"conexao": request.GET.get("conexao", ""), "status": request.GET.get("status", "")}
        if filtros["conexao"].isdigit():
            consulta = consulta.filter(conexao_id=filtros["conexao"])
        if filtros["status"] == "rascunho":
            consulta = consulta.filter(situacao_local=TemplateWhatsApp.RASCUNHO)
        elif filtros["status"]:
            consulta = consulta.filter(status_meta=filtros["status"].upper())
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return _pagina(self, request, "template_whatsapp_lista.html", {
            "templates_wa": pagina, "page_obj": pagina, "conexoes": conexoes, "filtros": filtros,
            "pode_criar": request.user.has_perm("integracao.add_templatewhatsapp"), "pode_alterar": request.user.has_perm("integracao.change_templatewhatsapp"),
            "pode_excluir": request.user.has_perm("integracao.delete_templatewhatsapp"),
        })


class TemplateFormView(View):
    """Criar (rascunho local) e editar o conteúdo. Nada vai à Meta aqui: o envio é uma ação explícita separada."""

    def _render(self, request, form, template=None):
        return _pagina(self, request, "template_whatsapp_form.html", {"form": form, "template_wa": template, "botoes_na_tela": range(1, BOTOES_NA_TELA + 1)})

    def get(self, request, pk=None):
        if pk is None:
            return self._render(request, TemplateWhatsAppForm(conexoes=_conexoes_templates(request, "add")))
        t = _template(request, pk, "change")
        return self._render(request, TemplateWhatsAppForm(initial=TemplateWhatsAppForm.inicial(t), conexoes=[t.conexao], travado=t.enviado), t)

    def post(self, request, pk=None):
        t = _template(request, pk, "change") if pk else None
        conexoes = [t.conexao] if t else _conexoes_templates(request, "add")
        form = TemplateWhatsAppForm(request.POST, initial=TemplateWhatsAppForm.inicial(t) if t else None, conexoes=conexoes, travado=bool(t and t.enviado))
        if form.is_valid():
            try:
                if t:
                    tw.editar_local(request, t, form.dados())
                else:
                    # A conexão vem do POST, mas só vale se estiver entre as autorizadas (as `choices` já a limitam; conferimos de novo no banco).
                    conexao = get_object_or_404(_conexoes_templates(request, "add"), pk=form.cleaned_data["conexao"])
                    t = tw.criar_local(request, conexao, form.dados())
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, "Template salvo aqui. Ele ainda não foi enviado à Meta." if not t.enviado else "Alterações salvas aqui. Envie à Meta para aplicá-las.")
                return redirect("template-whatsapp-list")
        return self._render(request, form, t)


class TemplateAcaoView(View):
    """POST: enviar (cria/atualiza na Meta) | excluir (na Meta e aqui). Permissão de cada ação conferida aqui e a unidade pelo escopo da consulta."""

    def post(self, request, pk, acao):
        if acao not in ("enviar", "excluir"):
            raise Http404
        permissao = "change" if acao == "enviar" else "delete"
        if not request.user.has_perm(f"integracao.{permissao}_templatewhatsapp"):
            raise PermissionDenied
        t = _template(request, pk, permissao)
        try:
            if acao == "enviar":
                t = tw.enviar_para_meta(request, t)
                messages.success(request, f"Enviado à Meta. Status informado por ela: {t.status_meta or 'sem retorno'}.")
            else:
                tw.excluir(request, t)
                messages.success(request, "Template excluído.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("template-whatsapp-list")


class TemplateSincronizarView(View):
    def post(self, request):
        pk = request.POST.get("conexao", "")
        conexao = get_object_or_404(_conexoes_templates(request, "change"), pk=pk if pk.isdigit() else 0)
        try:
            resumo = tw.sincronizar(request, conexao)
            messages.success(request, f"Sincronizado com a Meta: {resumo['importados']} novo(s), {resumo['atualizados']} atualizado(s), {resumo['ausentes']} não encontrado(s) lá.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("template-whatsapp-list")
