"""Canais de entrega de notificações (o "como enviar").

A regra de negócio (quem recebe, histórico, leitura) está em services.py e
não conhece o mecanismo de entrega. Para adicionar o Firebase Cloud
Messaging (app mobile), implemente `CanalFCM.enviar` e inclua-o em
`CANAIS`; nada mais muda:

    Web    -> CanalWebPush (django-webpush)
    Mobile -> CanalFCM     (a implementar)
"""
import json
import logging

from webpush.utils import send_notification_to_user

logger = logging.getLogger(__name__)


class ErroDeEntrega(Exception):
    """Falha ao entregar por um canal (a mensagem é gravada no histórico)."""


class CanalEntrega:
    nome = ""

    def enviar(self, usuario, titulo, mensagem, link):
        """Entrega a mensagem ao usuário. Levanta ErroDeEntrega se não conseguir."""
        raise NotImplementedError


class CanalWebPush(CanalEntrega):
    nome = "webpush"

    def enviar(self, usuario, titulo, mensagem, link):
        if not usuario.webpush_info.exists():
            raise ErroDeEntrega("Usuário sem dispositivo inscrito para notificações push.")
        payload = json.dumps({"head": titulo, "body": mensagem, "url": link or "/"})
        try:
            send_notification_to_user(usuario, payload)
        except Exception as erro:  # pywebpush levanta tipos variados (HTTP, rede, chave VAPID)
            logger.warning("Falha no web push para o usuário %s: %s", usuario.pk, erro)
            raise ErroDeEntrega(str(erro)[:250]) from erro


class CanalFCM(CanalEntrega):
    """Reservado para o app mobile (Firebase Cloud Messaging). Ainda não
    implementado: guardar o token do dispositivo e chamar a API do FCM."""

    nome = "fcm"

    def enviar(self, usuario, titulo, mensagem, link):
        raise ErroDeEntrega("Canal FCM ainda não implementado.")


# Canais ativos, em ordem. Basta UM entregar para a notificação contar como entregue.
CANAIS = [CanalWebPush()]


def entregar(usuario, titulo, mensagem, link):
    """Tenta cada canal; devolve None se algum entregou, senão o texto do último erro."""
    erro = "Nenhum canal de entrega configurado."
    for canal in CANAIS:
        try:
            canal.enviar(usuario, titulo, mensagem, link)
            return None
        except ErroDeEntrega as falha:
            erro = str(falha)
    return erro
