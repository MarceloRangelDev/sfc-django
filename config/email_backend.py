"""
Backend de e-mail do Django que envia mensagens usando a API do Resend
(https://resend.com) em vez de SMTP.

Como o EmailBackend do Django recebe uma lista de EmailMessage/EmailMultiAlternatives,
basta apontar `EMAIL_BACKEND` para esta classe (ver config/settings.py) que todo o
código existente que usa `django.core.mail.EmailMessage` / `send_mail` continua
funcionando normalmente, só que passando pelo Resend.
"""
import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class ResendEmailBackend(BaseEmailBackend):
    """Envia e-mails através da API HTTP do Resend."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        api_key = getattr(settings, "RESEND_API_KEY", None)
        if not api_key:
            if not self.fail_silently:
                raise ValueError(
                    "RESEND_API_KEY não está configurada. Defina essa variável no .env."
                )
            logger.warning("RESEND_API_KEY não configurada; e-mail(s) não enviado(s).")
            return 0

        try:
            import resend
        except ImportError as exc:  # pragma: no cover - dependência ausente
            if not self.fail_silently:
                raise ImportError(
                    "O pacote 'resend' não está instalado. Rode: pip install resend"
                ) from exc
            logger.exception("Pacote 'resend' não instalado; e-mail(s) não enviado(s).")
            return 0

        resend.api_key = api_key

        sent_count = 0
        for message in email_messages:
            try:
                self._send_one(resend, message)
                sent_count += 1
            except Exception:
                logger.exception("Falha ao enviar e-mail via Resend para %s", message.to)
                if not self.fail_silently:
                    raise

        return sent_count

    def _send_one(self, resend, message):
        # Corpo em texto puro (sempre presente)
        text_body = message.body

        # Se a mensagem tiver uma alternativa text/html (EmailMultiAlternatives),
        # usamos ela como corpo HTML do e-mail.
        html_body = None
        for content, mimetype in getattr(message, "alternatives", []) or []:
            if mimetype == "text/html":
                html_body = content
                break

        params = {
            "from": message.from_email,
            "to": list(message.to),
            "subject": message.subject,
            "text": text_body,
        }

        if html_body:
            params["html"] = html_body

        if message.cc:
            params["cc"] = list(message.cc)
        if message.bcc:
            params["bcc"] = list(message.bcc)
        if getattr(message, "reply_to", None):
            params["reply_to"] = list(message.reply_to)

        resend.Emails.send(params)
