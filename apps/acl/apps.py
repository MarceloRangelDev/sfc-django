from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _completar_permissoes_padrao(sender, **kwargs):
    """Depois de um `migrate`, completa as permissões padrão dos perfis nos módulos
    novos (só onde o perfil ainda não tem nenhuma permissão do módulo; nunca remove).
    Roda uma vez por `migrate`, na emissão do ÚLTIMO app, quando as permissões de todos
    os apps (inclusive os novos) já foram criadas. Num banco novo os perfis ainda não
    existem (o `seed_acl` os cria) e nada acontece."""
    from django.apps import apps

    # O Django só emite post_migrate para apps com models: "último" = o último deles.
    ultimo = [c for c in apps.get_app_configs() if c.models_module is not None][-1]
    if sender is not ultimo:
        return
    from .padroes import aplicar_padrao_aos_modulos

    aplicar_padrao_aos_modulos()


class AclConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.acl'

    def ready(self):
        post_migrate.connect(_completar_permissoes_padrao, dispatch_uid="acl_completar_permissoes_padrao")
