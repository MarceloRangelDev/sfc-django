from django.apps import AppConfig


class PrecificacaoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.precificacao'

    def ready(self):
        # Mantém as precificações automáticas em sincronia com a distribuição do custo operacional:
        # qualquer mudança em custos, ajustes por categoria ou vendas da categoria refaz o custo
        # operacional das precificações que não foram ajustadas à mão (uma única fonte de verdade).
        from django.db.models.signals import post_delete, post_save

        from apps.categoria.models import Categoria
        from apps.custo.models import CustoAjusteCategoria, CustoOperacional

        from .services import recalcular_custo_operacional_automatico

        def refazer(sender, instance, **kwargs):
            if kwargs.get("raw"):  # carga de fixtures: nada a recalcular
                return
            recalcular_custo_operacional_automatico(instance.cliente_id)

        for modelo in (CustoOperacional, CustoAjusteCategoria, Categoria):
            post_save.connect(refazer, sender=modelo, dispatch_uid=f"precificacao_refaz_{modelo.__name__}_salvo")
        for modelo in (CustoOperacional, CustoAjusteCategoria):
            post_delete.connect(refazer, sender=modelo, dispatch_uid=f"precificacao_refaz_{modelo.__name__}_apagado")
