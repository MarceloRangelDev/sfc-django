"""Instagram (Graph API da Meta): receber mensagens diretas e comentários por webhook e responder mensagens diretas.

ESCOPO REAL: webhook (mensagens e comentários → caixa de entrada + notificação) e envio de resposta por mensagem direta. PUBLICAÇÃO de
mídia NÃO está implementada. STATUS: implementado pela documentação pública; NÃO homologado — exige conta Instagram Business ligada a
uma página do Facebook, um app Meta APROVADO para as permissões (instagram_manage_messages, instagram_manage_comments) e revisão da
Meta. O cliente informou que ainda não há app aprovado.
"""
from .. import rede
from .base import Adaptador, ErroIntegracao, comparar, hmac_sha256


class InstagramGraph(Adaptador):
    chave = "instagram"
    rotulo = "Instagram (mensagens e comentários)"
    tipo = "social"
    descricao = "Recebe mensagens diretas e comentários do Instagram Business e responde mensagens diretas."
    validado = False
    escopos = ("instagram_manage_messages", "instagram_manage_comments")
    campos_credenciais = (("access_token", "Token de acesso da página/Instagram", True), ("app_secret", "Segredo do aplicativo (App Secret)", True), ("verify_token", "Token de verificação do webhook (você escolhe)", True))
    campos_config = (("ig_user_id", "ID da conta Instagram Business"),)
    suporta_webhook = True

    def _url(self, conexao, caminho):
        return f"https://graph.facebook.com/{conexao.config.get('api_version') or 'v21.0'}/{caminho}"

    def testar(self, conexao, credenciais):
        try:
            rede.requisitar("GET", self._url(conexao, conexao.config["ig_user_id"]), {"Authorization": f"Bearer {credenciais['access_token']}"})
        except rede.ErroDeRede as erro:
            raise ErroIntegracao(str(erro), temporario=erro.temporario, espera=erro.espera) from None

    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        corpo = {"recipient": {"id": destino}, "message": {"text": texto}}
        try:
            _, _, dados = rede.requisitar("POST", self._url(conexao, f"{conexao.config['ig_user_id']}/messages"), {"Authorization": f"Bearer {credenciais['access_token']}"}, corpo)
        except rede.ErroDeRede as erro:
            raise ErroIntegracao(str(erro), temporario=erro.temporario, espera=erro.espera) from None
        return str(dados.get("message_id", ""))

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
            for m in entrada.get("messaging", []) or []:
                mensagem = m.get("message") or {}
                if mensagem.get("mid"):
                    eventos.append({"id": mensagem["mid"], "tipo": "mensagem", "de": (m.get("sender") or {}).get("id", ""), "texto": (mensagem.get("text") or "")[:2000], "quando": m.get("timestamp")})
            for mudanca in entrada.get("changes", []) or []:
                valor = mudanca.get("value") or {}
                if mudanca.get("field") == "comments" and valor.get("id"):
                    eventos.append({"id": valor["id"], "tipo": "comentario", "de": (valor.get("from") or {}).get("id", ""), "texto": (valor.get("text") or "")[:2000], "quando": entrada.get("time")})
        return eventos
