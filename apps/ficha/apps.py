from django.apps import AppConfig


class FichaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ficha'

    def ready(self):
        # Liga as fichas ao motor de recálculo: alterar um insumo (ou um
        # processado) recalcula as fichas que o usam, na mesma transação.
        from apps.processado.recalculo import GANCHOS_POS_RECALCULO

        from .services import recalcular_fichas_dos_insumos

        if recalcular_fichas_dos_insumos not in GANCHOS_POS_RECALCULO:
            GANCHOS_POS_RECALCULO.append(recalcular_fichas_dos_insumos)
