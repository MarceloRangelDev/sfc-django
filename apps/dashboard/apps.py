from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "Painel"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from .presenca import registrar_saida

        # Quem sai do sistema deixa de aparecer como "online" na hora.
        user_logged_out.connect(registrar_saida, dispatch_uid="dashboard_presenca_logout")
