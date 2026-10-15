"""WhatsApp: (1) API oficial WhatsApp Business Cloud (Meta) e (2) provedor INTERMEDIÁRIO genérico (HTTP + assinatura HMAC).

Uso no sistema: ENVIAR notificações/mensagens (fila com reenvio) e RECEBER mensagens por webhook (caixa de entrada + notificação aos
usuários autorizados). Receber PEDIDOS = receber a mensagem e avisar a equipe; o sistema não interpreta texto livre para criar
pedido sozinho.

STATUS: implementado pela documentação pública; NÃO homologado (sem conta/credenciais). Exige conta WhatsApp Business verificada,
número, token e segredo do app (Cloud) ou credenciais do intermediário.
"""
import json

from .. import rede
from .base import Adaptador, ErroIntegracao, comparar, hmac_sha256


def _erro(erro):
    return ErroIntegracao(str(erro), temporario=erro.temporario, espera=erro.espera)


class WhatsAppCloud(Adaptador):
    chave = "whatsapp_cloud"
    rotulo = "WhatsApp Business (API oficial da Meta)"
    tipo = "mensagem"
    descricao = "Envia e recebe mensagens pela API de nuvem oficial do WhatsApp Business."
    validado = False
    escopos = ("whatsapp_business_messaging",)
    campos_credenciais = (("access_token", "Token de acesso permanente", True), ("app_secret", "Segredo do aplicativo (App Secret)", True), ("verify_token", "Token de verificação do webhook (você escolhe)", True))
    campos_config = (("phone_number_id", "ID do número de telefone (Phone Number ID)"),)
    suporta_webhook = True

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


def corpo_json(dados):
    return json.dumps(dados, separators=(",", ":")).encode()
