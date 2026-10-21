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

        # Mão de obra na ficha: mudar o valor da hora de um colaborador refaz as fichas (e precificações) que o usam.
        from django.db.models.signals import post_save

        from apps.maodeobra.models import Colaborador

        from .services import recalcular_fichas_do_colaborador

        def ao_salvar_colaborador(sender, instance, created, **kwargs):
            if not created:
                recalcular_fichas_do_colaborador(instance)

        post_save.connect(ao_salvar_colaborador, sender=Colaborador, dispatch_uid="ficha_recalcula_ao_salvar_colaborador")
