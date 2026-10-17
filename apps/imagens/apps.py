from django.apps import AppConfig


class ImagensConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imagens"
    verbose_name = "Imagens"

    def ready(self):
        from . import signals  # noqa: F401  (desvincula as imagens quando a entidade é excluída)
