"""Concede ao perfil Administrador as permissões do Kanban (os demais perfis só por concessão explícita)."""
from django.db import migrations


def conceder(apps, schema_editor):
    from django.apps import apps as django_apps
    from django.contrib.auth.management import create_permissions

    create_permissions(django_apps.get_app_config("kanban"), apps=apps, verbosity=0)
    Permission, Group = apps.get_model("auth", "Permission"), apps.get_model("auth", "Group")
    permissoes = Permission.objects.filter(content_type__app_label="kanban")
    for grupo in Group.objects.filter(name="Administrador"):
        grupo.permissions.add(*permissoes)


class Migration(migrations.Migration):
    dependencies = [("kanban", "0001_initial")]
    operations = [migrations.RunPython(conceder, migrations.RunPython.noop)]
