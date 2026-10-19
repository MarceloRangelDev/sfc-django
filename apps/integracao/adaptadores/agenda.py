"""Calendários: Google Agenda (OAuth2 + Calendar API) e iCloud (CalDAV com senha específica de app).

Escopo mínimo: Google `calendar.events` (só eventos, não toda a conta). O token de acesso expira (~1 h) e é RENOVADO com o
refresh token (`renovar`); `revogar` invalida no Google. iCloud não tem OAuth: o usuário gera uma "senha específica de app" no
ID Apple e informa a URL do calendário CalDAV.

STATUS: implementado pela documentação pública; NÃO homologado (sem conta/credenciais). Google exige um projeto no Google Cloud com a
API Calendar habilitada e o cliente OAuth (GOOGLE_OAUTH_CLIENT_ID/SECRET no .env).
"""
import base64
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

from django.conf import settings

from .. import rede
from .base import Adaptador, ErroIntegracao


def _erro(erro):
    return ErroIntegracao(str(erro), temporario=erro.temporario, espera=erro.espera)


def _iso(valor):
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


class GoogleCalendar(Adaptador):
    chave = "google_calendar"
    rotulo = "Google Agenda"
    tipo = "agenda"
    descricao = "Sincroniza vencimentos de contas, entregas, reservas e compromissos com o Google Agenda (leitura e escrita)."
    validado = False
    escopos = ("https://www.googleapis.com/auth/calendar.events",)
    campos_credenciais = ()
    campos_config = (("calendar_id", "ID do calendário (use primary para o principal)"),)
    usa_oauth = True

    URL_AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
    URL_TOKEN = "https://oauth2.googleapis.com/token"
    URL_REVOGAR = "https://oauth2.googleapis.com/revoke"
    URL_API = "https://www.googleapis.com/calendar/v3/calendars"

    def configurado(self):
        return bool(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "") and getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""))

    def url_autorizacao(self, estado, redirect_uri):
        params = {"client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(self.escopos),
                  "access_type": "offline", "prompt": "consent", "state": estado, "include_granted_scopes": "true"}
        return self.URL_AUTORIZAR + "?" + urllib.parse.urlencode(params)

    def trocar_codigo(self, codigo, redirect_uri):
        """Troca o código de autorização por tokens. Devolve (credenciais, expira_em)."""
        corpo = urllib.parse.urlencode({"code": codigo, "client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                                        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        try:
            _, _, dados = rede.requisitar("POST", self.URL_TOKEN, {"Content-Type": "application/x-www-form-urlencoded"}, corpo)
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None
        if not dados.get("access_token"):
            raise ErroIntegracao("O Google não devolveu o token de acesso.")
        return ({"access_token": dados["access_token"], "refresh_token": dados.get("refresh_token", "")}, datetime.now(timezone.utc) + timedelta(seconds=int(dados.get("expires_in", 3600))))

    def renovar(self, conexao, credenciais):
        if not credenciais.get("refresh_token"):
            raise ErroIntegracao("Sem refresh token: reconecte a conta.")
        corpo = urllib.parse.urlencode({"client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                                        "refresh_token": credenciais["refresh_token"], "grant_type": "refresh_token"})
        try:
            _, _, dados = rede.requisitar("POST", self.URL_TOKEN, {"Content-Type": "application/x-www-form-urlencoded"}, corpo)
        except rede.ErroDeRede as erro:
            raise ErroIntegracao("Não foi possível renovar o acesso (reconecte a conta se persistir).", temporario=erro.temporario, espera=erro.espera) from None
        return {**credenciais, "access_token": dados["access_token"]}, datetime.now(timezone.utc) + timedelta(seconds=int(dados.get("expires_in", 3600)))

    def revogar(self, conexao, credenciais):
        token = credenciais.get("refresh_token") or credenciais.get("access_token")
        if token:
            try:
                rede.requisitar("POST", self.URL_REVOGAR, {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode({"token": token}), json_resposta=False)
            except rede.ErroDeRede:
                pass  # melhor esforço: as credenciais locais são apagadas de qualquer forma

    def _cab(self, credenciais):
        return {"Authorization": f"Bearer {credenciais['access_token']}"}

    def _cal(self, conexao):
        return urllib.parse.quote(conexao.config.get("calendar_id") or "primary", safe="")

    def _corpo(self, evento):
        if evento.get("todo_o_dia"):
            inicio, fim = {"date": evento["inicio"].date().isoformat()}, {"date": (evento["fim"].date() + timedelta(days=1)).isoformat()}
        else:
            inicio, fim = {"dateTime": _iso(evento["inicio"])}, {"dateTime": _iso(evento["fim"])}
        return {"summary": evento["titulo"][:200], "description": (evento.get("descricao") or "")[:1000], "start": inicio, "end": fim}

    def _chamar(self, metodo, url, credenciais, corpo=None):
        try:
            return rede.requisitar(metodo, url, self._cab(credenciais), corpo, json_resposta=metodo != "DELETE")
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def testar(self, conexao, credenciais):
        # O escopo pedido (calendar.events) não permite ler os metadados do calendário (calendars.get → 403); listar 1 evento permite.
        self._chamar("GET", f"{self.URL_API}/{self._cal(conexao)}/events?maxResults=1", credenciais)

    def criar_evento(self, conexao, credenciais, evento):
        _, _, dados = self._chamar("POST", f"{self.URL_API}/{self._cal(conexao)}/events", credenciais, self._corpo(evento))
        return dados["id"], dados.get("etag", "")

    def atualizar_evento(self, conexao, credenciais, id_externo, evento):
        _, _, dados = self._chamar("PATCH", f"{self.URL_API}/{self._cal(conexao)}/events/{urllib.parse.quote(id_externo, safe='')}", credenciais, self._corpo(evento))
        return dados.get("etag", "")

    def remover_evento(self, conexao, credenciais, id_externo):
        try:
            self._chamar("DELETE", f"{self.URL_API}/{self._cal(conexao)}/events/{urllib.parse.quote(id_externo, safe='')}", credenciais)
        except ErroIntegracao as erro:
            if "404" not in str(erro) and "410" not in str(erro):
                raise

    def listar_eventos(self, conexao, credenciais, desde, ate):
        params = urllib.parse.urlencode({"timeMin": _iso(desde), "timeMax": _iso(ate), "singleEvents": "true", "maxResults": 250, "showDeleted": "false"})
        _, _, dados = self._chamar("GET", f"{self.URL_API}/{self._cal(conexao)}/events?{params}", credenciais)
        eventos = []
        for item in dados.get("items", []):
            inicio, fim = item.get("start", {}), item.get("end", {})
            todo = "date" in inicio
            eventos.append({
                "id": item["id"], "titulo": item.get("summary", "(sem título)"), "descricao": item.get("description", ""), "todo_o_dia": todo,
                "inicio": inicio.get("dateTime") or inicio.get("date"), "fim": fim.get("dateTime") or fim.get("date"), "atualizado_em": item.get("updated"), "etag": item.get("etag", ""),
            })
        return eventos


class GoogleTasks(GoogleCalendar):
    """Google Tarefas (Tasks API v1) no lugar do Google Keep: a API do Keep é voltada a administradores Workspace e não edita notas.

    Sincroniza os compromissos do tipo TAREFA nos dois sentidos (título, notas, data e concluído). Limite da API: o campo `due` guarda
    só a DATA (a hora é descartada), então a tarefa vira "dia inteiro" no Google. Escopo `tasks` (leitura e escrita).
    STATUS: implementado pela documentação (consultada em 18/10/2026); NÃO homologado com conta real."""

    chave = "google_tasks"
    rotulo = "Google Tarefas"
    tipo = "agenda"
    descricao = "Sincroniza as tarefas da agenda com o Google Tarefas, nos dois sentidos (inclui marcar como concluída)."
    escopos = ("https://www.googleapis.com/auth/tasks",)
    campos_config = (("tasklist", "ID da lista de tarefas (use @default para a lista principal)"),)
    so_tarefas = True
    sincroniza_conclusao = True
    URL_API = "https://tasks.googleapis.com/tasks/v1/lists"

    def _chamar(self, metodo, url, credenciais, corpo=None):
        try:
            return super()._chamar(metodo, url, credenciais, corpo)
        except ErroIntegracao as erro:
            if "403" in str(erro):  # causa comum: API desligada no projeto ou escopo ausente na tela de consentimento
                raise ErroIntegracao("O Google recusou o acesso (403). Ative a \"Google Tasks API\" no projeto do Google Cloud e confirme que o escopo "
                                     "\"tasks\" está na tela de consentimento; depois reconecte a conta.") from None
            raise

    def _lista(self, conexao):
        return urllib.parse.quote(conexao.config.get("tasklist") or "@default", safe="@")

    def _corpo(self, evento):
        concluido = bool(evento.get("concluido"))
        corpo = {"title": evento["titulo"][:1000], "notes": (evento.get("descricao") or "")[:8000],
                 "due": evento["inicio"].strftime("%Y-%m-%dT00:00:00.000Z"), "status": "completed" if concluido else "needsAction"}
        if not concluido:
            corpo["completed"] = None  # reabrir exige limpar a data de conclusão
        return corpo

    def testar(self, conexao, credenciais):
        self._chamar("GET", f"{self.URL_API}/{self._lista(conexao)}/tasks?maxResults=1", credenciais)

    def criar_evento(self, conexao, credenciais, evento):
        _, _, dados = self._chamar("POST", f"{self.URL_API}/{self._lista(conexao)}/tasks", credenciais, self._corpo(evento))
        return dados["id"], dados.get("etag", "")

    def atualizar_evento(self, conexao, credenciais, id_externo, evento):
        _, _, dados = self._chamar("PATCH", f"{self.URL_API}/{self._lista(conexao)}/tasks/{urllib.parse.quote(id_externo, safe='')}", credenciais, self._corpo(evento))
        return dados.get("etag", "")

    def remover_evento(self, conexao, credenciais, id_externo):
        try:
            self._chamar("DELETE", f"{self.URL_API}/{self._lista(conexao)}/tasks/{urllib.parse.quote(id_externo, safe='')}", credenciais)
        except ErroIntegracao as erro:
            if "404" not in str(erro) and "410" not in str(erro):
                raise

    def listar_eventos(self, conexao, credenciais, desde, ate):
        """Tarefas com data dentro da janela (as sem data são ignoradas: não há onde mostrá-las na agenda). Até 5 páginas de 100."""
        eventos, token = [], ""
        for _ in range(5):
            params = {"maxResults": 100, "showCompleted": "true", "showHidden": "true", "dueMin": _iso(desde), "dueMax": _iso(ate)}
            if token:
                params["pageToken"] = token
            _, _, dados = self._chamar("GET", f"{self.URL_API}/{self._lista(conexao)}/tasks?{urllib.parse.urlencode(params)}", credenciais)
            for item in dados.get("items", []):
                if not item.get("due") or item.get("deleted"):
                    continue
                dia = item["due"][:10]
                eventos.append({"id": item["id"], "titulo": item.get("title") or "(sem título)", "descricao": item.get("notes", ""), "todo_o_dia": True,
                                "inicio": dia, "fim": dia, "atualizado_em": item.get("updated"), "etag": item.get("etag", ""), "concluido": item.get("status") == "completed"})
            token = dados.get("nextPageToken", "")
            if not token:
                break
        return eventos


class ICloudCalendar(Adaptador):
    chave = "icloud_calendar"
    rotulo = "Calendário do iCloud (CalDAV)"
    tipo = "agenda"
    descricao = "Sincroniza eventos com um calendário do iCloud por CalDAV, usando uma senha específica de app (não a senha do Apple ID)."
    validado = False
    campos_credenciais = (("usuario", "ID Apple (e-mail)", False), ("senha_app", "Senha específica de app", True))
    campos_config = (("calendario_url", "URL do calendário CalDAV (https)"),)

    def validar(self, credenciais, config):
        super().validar(credenciais, config)
        try:
            rede.url_segura(config["calendario_url"])
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None

    def _cab(self, credenciais, extra=None):
        token = base64.b64encode(f"{credenciais['usuario']}:{credenciais['senha_app']}".encode()).decode()
        return {"Authorization": f"Basic {token}", **(extra or {})}

    def _url(self, conexao, uid):
        return rede.url_segura(conexao.config["calendario_url"].rstrip("/") + f"/{urllib.parse.quote(uid, safe='')}.ics")

    def _ics(self, uid, evento):
        def dt(v, todo):
            if todo:
                return f";VALUE=DATE:{v.strftime('%Y%m%d')}"
            return f":{v.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

        def esc(t):
            return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

        todo = bool(evento.get("todo_o_dia"))
        fim = evento["fim"] + timedelta(days=1) if todo else evento["fim"]
        return "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SFC//Integracao//PT", "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART{dt(evento['inicio'], todo)}", f"DTEND{dt(fim, todo)}", f"SUMMARY:{esc(evento['titulo'][:200])}", f"DESCRIPTION:{esc((evento.get('descricao') or '')[:1000])}",
            "END:VEVENT", "END:VCALENDAR", ""])

    def _put(self, conexao, credenciais, uid, evento):
        try:
            rede.requisitar("PUT", self._url(conexao, uid), self._cab(credenciais, {"Content-Type": "text/calendar; charset=utf-8"}), self._ics(uid, evento), json_resposta=False)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def testar(self, conexao, credenciais):
        try:
            rede.requisitar("PROPFIND", rede.url_segura(conexao.config["calendario_url"]), self._cab(credenciais, {"Depth": "0"}), json_resposta=False)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def criar_evento(self, conexao, credenciais, evento):
        uid = f"sfc-{uuid.uuid4()}"
        self._put(conexao, credenciais, uid, evento)
        return uid, ""

    def atualizar_evento(self, conexao, credenciais, id_externo, evento):
        self._put(conexao, credenciais, id_externo, evento)
        return ""

    def remover_evento(self, conexao, credenciais, id_externo):
        try:
            rede.requisitar("DELETE", self._url(conexao, id_externo), self._cab(credenciais), json_resposta=False)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            if erro.status != 404:
                raise _erro(erro) from None

    def listar_eventos(self, conexao, credenciais, desde, ate):
        """REPORT calendar-query no intervalo; lê UID, SUMMARY, DESCRIPTION, DTSTART/DTEND e LAST-MODIFIED de cada VEVENT."""
        formato = "%Y%m%dT%H%M%SZ"
        corpo = (
            '<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/></d:prop>'
            f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{desde.astimezone(timezone.utc).strftime(formato)}" end="{ate.astimezone(timezone.utc).strftime(formato)}"/>'
            "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        )
        try:
            _, _, bruto = rede.requisitar("REPORT", rede.url_segura(conexao.config["calendario_url"]), self._cab(credenciais, {"Depth": "1", "Content-Type": "application/xml"}), corpo, json_resposta=False)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None
        return [e for e in (_ler_vevent(b) for b in re.findall(r"BEGIN:VEVENT.*?END:VEVENT", bruto.decode("utf-8", "replace"), re.S)) if e]


def _ler_vevent(bloco):
    def campo(nome):
        m = re.search(rf"^{nome}[^:\r\n]*:(.*)$", bloco, re.M)
        return m.group(1).strip().replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";") if m else ""

    uid = campo("UID")
    if not uid:
        return None
    todo = "VALUE=DATE" in (re.search(r"^DTSTART[^:\r\n]*", bloco, re.M) or [""])[0] if re.search(r"^DTSTART", bloco, re.M) else False
    return {"id": uid, "titulo": campo("SUMMARY") or "(sem título)", "descricao": campo("DESCRIPTION"), "todo_o_dia": todo, "inicio": campo("DTSTART"), "fim": campo("DTEND"),
            "atualizado_em": campo("LAST-MODIFIED") or None, "etag": ""}
