"""Concede ao perfil Administrador as permissões do cadastro global de insumos (os demais perfis só por concessão explícita)."""
from django.db import migrations


def conceder(apps, schema_editor):
    """Cria as permissões novas e as concede aos papéis existentes (nada muda para quem já usava o sistema)."""
    from django.apps import apps as django_apps
    from django.contrib.auth.management import create_permissions

    create_permissions(django_apps.get_app_config("insumoglobal"), apps=apps, verbosity=0)
    Permission, Group = apps.get_model("auth", "Permission"), apps.get_model("auth", "Group")
    permissoes = Permission.objects.filter(content_type__app_label="insumoglobal")
    for grupo in Group.objects.filter(name="Administrador"):
        grupo.permissions.add(*permissoes)


class Migration(migrations.Migration):
    dependencies = [("insumoglobal", "0001_initial")]
    operations = [migrations.RunPython(conceder, migrations.RunPython.noop)]
