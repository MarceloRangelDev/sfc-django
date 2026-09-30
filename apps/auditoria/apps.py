from django.apps import AppConfig


class AuditoriaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.auditoria'

    def ready(self):
        # Conecta os sinais de login/logout/login-falho (ver signals.py) —
        # precisa ser importado aqui para os @receiver serem registrados.
        from . import signals  # noqa: F401
