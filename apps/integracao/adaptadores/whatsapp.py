"""WhatsApp: (1) API oficial WhatsApp Business Cloud (Meta) e (2) provedor INTERMEDIÁRIO genérico (HTTP + assinatura HMAC).

Uso no sistema: ENVIAR notificações/mensagens (fila com reenvio) e RECEBER mensagens por webhook (caixa de entrada + notificação aos
usuários autorizados). Receber PEDIDOS = receber a mensagem e avisar a equipe; o sistema não interpreta texto livre para criar
pedido sozinho.

STATUS: implementado pela documentação pública; NÃO homologado (sem conta/credenciais). Exige conta WhatsApp Business verificada,
número, token e segredo do app (Cloud) ou credenciais do intermediário.
"""
import json
import re
from urllib.parse import quote

from .. import rede
from .base import Adaptador, ErroIntegracao, comparar, hmac_sha256


def _erro(erro):
    return ErroIntegracao(str(erro), temporario=erro.temporario, espera=erro.espera)


def _erro_template(erro):
    """Como `_erro`, mas inclui o motivo informado pela Meta (ex.: "texto do corpo inválido"), útil ao editar templates."""
    return ErroIntegracao(f"{erro} {erro.detalhe}".strip(), temporario=erro.temporario, espera=erro.espera)


class WhatsAppCloud(Adaptador):
    chave = "whatsapp_cloud"
    rotulo = "WhatsApp Business (API oficial da Meta)"
    tipo = "mensagem"
    descricao = "Envia e recebe mensagens pela API de nuvem oficial do WhatsApp Business."
    validado = False
    escopos = ("whatsapp_business_messaging",)
    campos_credenciais = (("access_token", "Token de acesso permanente", True), ("app_secret", "Segredo do aplicativo (App Secret)", True), ("verify_token", "Token de verificação do webhook (você escolhe)", True))
    campos_config = (("phone_number_id", "ID do número de telefone (Phone Number ID)"),)
    # WABA: conta do WhatsApp Business dona dos templates. Opcional para não invalidar conexões já criadas; sem ele só os templates ficam indisponíveis.
    campos_config_opcionais = (("waba_id", "ID da conta do WhatsApp Business (WABA) — necessário para os templates"),)
    suporta_webhook = True

    def validar(self, credenciais, config):
        super().validar(credenciais, config)
        if config.get("waba_id") and not re.fullmatch(r"\d{5,25}", str(config["waba_id"])):
            raise ErroIntegracao("O ID da conta do WhatsApp Business (WABA) deve conter apenas números.")

    # --- templates de mensagem (Graph API /{waba_id}/message_templates) ------------------
    CAMPOS_TEMPLATE = "id,name,language,category,status,components,rejected_reason"

    def _waba(self, conexao):
        waba = conexao.config.get("waba_id")
        if not waba:
            raise ErroIntegracao("Informe o ID da conta do WhatsApp Business (WABA) em Alterar credenciais para usar templates.")
        return waba

    def _graph(self, conexao, credenciais, metodo, caminho, corpo=None):
        try:
            return rede.requisitar(metodo, self._url(conexao, caminho), {"Authorization": f"Bearer {credenciais['access_token']}"}, corpo)[2]
        except rede.ErroDeRede as erro:
            raise _erro_template(erro) from None

    def listar_templates(self, conexao, credenciais):
        """Todos os templates da conta (segue a paginação da Graph API, com teto de segurança)."""
        caminho, itens = f"{self._waba(conexao)}/message_templates?limit=100&fields={self.CAMPOS_TEMPLATE}", []
        for _ in range(20):
            dados = self._graph(conexao, credenciais, "GET", caminho) or {}
            itens += dados.get("data") or []
            depois = ((dados.get("paging") or {}).get("cursors") or {}).get("after")
            if not depois or not (dados.get("paging") or {}).get("next"):
                break
            caminho = f"{self._waba(conexao)}/message_templates?limit=100&fields={self.CAMPOS_TEMPLATE}&after={quote(str(depois), safe='')}"
        return itens

    def criar_template(self, conexao, credenciais, template):
        """template: {"name","language","category","components"}. Devolve {"id","status","category"} COMO A META RESPONDEU."""
        dados = self._graph(conexao, credenciais, "POST", f"{self._waba(conexao)}/message_templates", template)
        if not isinstance(dados, dict) or not dados.get("id"):
            raise ErroIntegracao("Resposta inesperada da Meta ao criar o template.")
        return dados

    def atualizar_template(self, conexao, credenciais, id_externo, template):
        dados = self._graph(conexao, credenciais, "POST", quote(str(id_externo), safe=""), template)
        if not isinstance(dados, dict) or not dados.get("success"):
            raise ErroIntegracao("A Meta não confirmou a atualização do template.")

    def obter_template(self, conexao, credenciais, id_externo):
        return self._graph(conexao, credenciais, "GET", f"{quote(str(id_externo), safe='')}?fields={self.CAMPOS_TEMPLATE}")

    def excluir_template(self, conexao, credenciais, nome, id_externo=None):
        """Sem `id_externo` a Meta exclui o template em TODOS os idiomas; com ele, só aquele idioma."""
        consulta = f"name={quote(nome, safe='')}" + (f"&hsm_id={quote(str(id_externo), safe='')}" if id_externo else "")
        dados = self._graph(conexao, credenciais, "DELETE", f"{self._waba(conexao)}/message_templates?{consulta}")
        if not isinstance(dados, dict) or not dados.get("success"):
            raise ErroIntegracao("A Meta não confirmou a exclusão do template.")

    def _url(self, conexao, caminho):
        versao = conexao.config.get("api_version") or "v21.0"
        return f"https://graph.facebook.com/{versao}/{caminho}"

    def testar(self, conexao, credenciais):
        try:
            rede.requisitar("GET", self._url(conexao, conexao.config["phone_number_id"]), {"Authorization": f"Bearer {credenciais['access_token']}"})
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        corpo = {"messaging_product": "whatsapp", "to": destino, "type": "text", "text": {"body": texto}}
        try:
            _, _, dados = rede.requisitar("POST", self._url(conexao, f"{conexao.config['phone_number_id']}/messages"), {"Authorization": f"Bearer {credenciais['access_token']}"}, corpo)
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None
        try:
            return dados["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            raise ErroIntegracao("Resposta inesperada do WhatsApp.") from None

    def desafio_webhook(self, conexao, credenciais, parametros):
        if parametros.get("hub.mode") == "subscribe" and comparar(parametros.get("hub.verify_token"), credenciais.get("verify_token")):
            return parametros.get("hub.challenge")
        return None

    def assinatura_valida(self, conexao, credenciais, cabecalhos, corpo):
        recebida = (cabecalhos.get("X-Hub-Signature-256") or "").replace("sha256=", "")
        return bool(credenciais.get("app_secret")) and comparar(recebida, hmac_sha256(credenciais["app_secret"], corpo))

    def interpretar_webhook(self, conexao, payload):
        eventos = []
        for entrada in payload.get("entry", []):
            for mudanca in entrada.get("changes", []):
                for mensagem in (mudanca.get("value") or {}).get("messages", []) or []:
                    eventos.append({"id": mensagem.get("id", ""), "tipo": "mensagem", "de": mensagem.get("from", ""), "texto": ((mensagem.get("text") or {}).get("body") or "")[:2000], "quando": mensagem.get("timestamp")})
        return eventos


class WhatsAppIntermediario(Adaptador):
    chave = "whatsapp_intermediario"
    rotulo = "WhatsApp por provedor intermediário"
    tipo = "mensagem"
    descricao = "Envia e recebe mensagens por um provedor intermediário que ofereça API HTTP (informe a URL base e o token)."
    validado = False
    campos_credenciais = (("token", "Token da API do provedor", True), ("segredo_webhook", "Segredo do webhook (HMAC-SHA256)", True))
    campos_config = (("base_url", "URL base da API (https)"),)
    suporta_webhook = True

    def validar(self, credenciais, config):
        super().validar(credenciais, config)
        try:
            rede.url_segura(config["base_url"])
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None

    def _chamar(self, conexao, credenciais, metodo, caminho, corpo=None):
        try:
            url = rede.url_segura(conexao.config["base_url"].rstrip("/") + caminho)
            return rede.requisitar(metodo, url, {"Authorization": f"Bearer {credenciais['token']}"}, corpo)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def testar(self, conexao, credenciais):
        self._chamar(conexao, credenciais, "GET", "/status")

    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        _, _, dados = self._chamar(conexao, credenciais, "POST", "/mensagens", {"para": destino, "texto": texto})
        return str((dados or {}).get("id", ""))

    def assinatura_valida(self, conexao, credenciais, cabecalhos, corpo):
        segredo = credenciais.get("segredo_webhook")
        return bool(segredo) and comparar((cabecalhos.get("X-Assinatura") or "").replace("sha256=", ""), hmac_sha256(segredo, corpo))

    def interpretar_webhook(self, conexao, payload):
        itens = payload.get("mensagens") if isinstance(payload.get("mensagens"), list) else [payload]
        return [{"id": str(i.get("id", "")), "tipo": "mensagem", "de": str(i.get("de", "")), "texto": str(i.get("texto", ""))[:2000], "quando": i.get("timestamp")} for i in itens if i.get("id")]


class WhatsAppEvolution(WhatsAppIntermediario):
    """Evolution API v2 (https://doc.evolution-api.com), instalada pelo próprio cliente. Reaproveita do intermediário genérico a validação da
    URL base (HTTPS público, anti-SSRF) e o tratamento de erros; muda o contrato:

    - autenticação por cabeçalho `apikey` (chave da instância ou chave global do servidor);
    - enviar: POST /message/sendText/{instancia} {"number", "text"} -> {"key": {"id"}};
    - testar: GET /instance/connectionState/{instancia} -> {"instance": {"state": "open"}};
    - receber: webhook `messages.upsert`. A Evolution NÃO assina o corpo com HMAC; em vez disso ela repete, em cada chamada, os cabeçalhos
      personalizados cadastrados no webhook. Por isso o webhook deve ser cadastrado com o cabeçalho `X-Webhook-Secret` = segredo guardado aqui
      (comparado em tempo constante). Sem o cabeçalho correto tudo é recusado (a regra de nunca aceitar webhook sem autenticação vale igual).

    Isolamento multi-tenant: cada conexão pertence a uma unidade e tem token de URL próprio; além do segredo, só eventos da `instancia`
    configurada nesta conexão são aceitos, então outra instância apontada para a mesma URL não injeta mensagens.
    STATUS: escrito pela documentação pública; NÃO homologado contra um servidor real."""

    chave = "whatsapp_evolution"
    rotulo = "WhatsApp via Evolution API (v2)"
    descricao = "Envia e recebe mensagens por uma instância da Evolution API v2 (informe a URL base, o nome da instância e a apikey)."
    campos_credenciais = (("apikey", "Chave de API (apikey) da instância", True), ("segredo_webhook", "Segredo do webhook (cabeçalho X-Webhook-Secret)", True))
    campos_config = (("base_url", "URL base da Evolution API (https)"), ("instancia", "Nome da instância"))
    # Nome usado em caminho de URL: restringe a caracteres seguros (sem "/", "?", "#", "..").
    _NOME_INSTANCIA = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

    def validar(self, credenciais, config):
        super().validar(credenciais, config)
        if not self._NOME_INSTANCIA.match(str(config.get("instancia", ""))) or str(config["instancia"]).strip(".") == "":
            raise ErroIntegracao("Nome da instância inválido: use letras, números, ponto, hífen ou sublinhado.")

    def _chamar(self, conexao, credenciais, metodo, caminho, corpo=None):
        try:
            url = rede.url_segura(conexao.config["base_url"].rstrip("/") + caminho)
            return rede.requisitar(metodo, url, {"apikey": credenciais["apikey"]}, corpo)
        except ValueError as erro:
            raise ErroIntegracao(str(erro)) from None
        except rede.ErroDeRede as erro:
            raise _erro(erro) from None

    def _instancia(self, conexao):
        return quote(str(conexao.config["instancia"]), safe="")

    def testar(self, conexao, credenciais):
        _, _, dados = self._chamar(conexao, credenciais, "GET", f"/instance/connectionState/{self._instancia(conexao)}")
        estado = ((dados or {}).get("instance") or {}).get("state") if isinstance(dados, dict) else None
        if estado != "open":
            raise ErroIntegracao(f"A instância não está conectada ao WhatsApp (estado: {str(estado or 'desconhecido')[:30]}). Leia o QR Code no painel da Evolution.")

    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        corpo = {"number": str(destino).lstrip("+"), "text": texto}
        _, _, dados = self._chamar(conexao, credenciais, "POST", f"/message/sendText/{self._instancia(conexao)}", corpo)
        try:
            return str(dados["key"]["id"])
        except (KeyError, TypeError):
            raise ErroIntegracao("Resposta inesperada da Evolution API.") from None

    def assinatura_valida(self, conexao, credenciais, cabecalhos, corpo):
        segredo = credenciais.get("segredo_webhook")
        return bool(segredo) and comparar(cabecalhos.get("X-Webhook-Secret"), segredo)

    def interpretar_webhook(self, conexao, payload):
        # O nome do evento chega como "messages.upsert" ou "MESSAGES_UPSERT" conforme a configuração do webhook.
        if str(payload.get("event", "")).lower().replace("_", ".") != "messages.upsert":
            return []
        if payload.get("instance") != conexao.config.get("instancia"):
            return []
        dados = payload.get("data")
        eventos = []
        for item in dados if isinstance(dados, list) else [dados]:
            if not isinstance(item, dict):
                continue
            chave = item.get("key") or {}
            numero = chave.get("remoteJid") or ""
            # Ignora o que o próprio número enviou e conversas de grupo/difusão (só chats individuais).
            if chave.get("fromMe") or not numero.endswith("@s.whatsapp.net") or not chave.get("id"):
                continue
            mensagem = item.get("message") or {}
            texto = mensagem.get("conversation") or (mensagem.get("extendedTextMessage") or {}).get("text") or ""
            eventos.append({"id": str(chave["id"]), "tipo": "mensagem", "de": numero.split("@")[0], "texto": str(texto)[:2000], "quando": item.get("messageTimestamp")})
        return eventos


def corpo_json(dados):
    return json.dumps(dados, separators=(",", ":")).encode()
