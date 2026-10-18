import logging

from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in

logger = logging.getLogger(__name__)


def _resumo_ao_entrar(sender, request, user, **kwargs):
    """Ao entrar, avisa uma vez por dia os compromissos de hoje/atrasados. Falha aqui NUNCA impede o login."""
    try:
        from .lembretes import resumo_do_dia

        resumo_do_dia(user)
    except Exception:
        logger.exception("Falha ao gerar o resumo da agenda")


class IntegracaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integracao"
    verbose_name = "Integrações"

    def ready(self):
        user_logged_in.connect(_resumo_ao_entrar, dispatch_uid="agenda_resumo_ao_entrar")
