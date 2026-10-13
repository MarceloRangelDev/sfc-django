from django.apps import AppConfig


def _normalizar_insumos_gerados(sender, **kwargs):
    """Depois do `migrate`, converte insumos gerados que ficaram no formato antigo (ex.:
    "500 g a R$ 39,90") para "1 kg ao custo do quilo". Roda uma vez por migrate (na emissão
    do último app com models, quando todas as tabelas estão prontas), é idempotente e
    NUNCA derruba o migrate: falhas só vão para o log (o comando `recalcular_custos` refaz)."""
    import logging

    from django.apps import apps

    ultimo = [c for c in apps.get_app_configs() if c.models_module is not None][-1]
    if sender is not ultimo:
        return
    try:
        from .recalculo import normalizar_insumos_gerados

        normalizar_insumos_gerados()
    except Exception:  # noqa: BLE001 — não pode impedir o migrate
        logging.getLogger(__name__).exception("Não foi possível normalizar os insumos gerados por processados")


class ProcessadoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.processado'

    def ready(self):
        from django.db.models.signals import post_migrate, post_save

        from apps.insumo.models import Insumo

        from .signals import propagar_alteracao_de_insumo

        post_save.connect(propagar_alteracao_de_insumo, sender=Insumo, dispatch_uid="processado_propaga_insumo")
        post_migrate.connect(_normalizar_insumos_gerados, dispatch_uid="processado_normaliza_insumos_gerados")
