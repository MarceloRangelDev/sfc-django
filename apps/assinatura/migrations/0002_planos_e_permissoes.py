"""Planos iniciais (valores em centavos: mensal R$ 59,90 e anual R$ 429,00 — editáveis depois no banco/admin) e permissões."""
from django.db import migrations

PLANOS = [("mensal", "Faturando Certo Mensal", 5990, "month", 1), ("anual", "Faturando Certo Anual", 42900, "year", 1)]


def aplicar(apps, schema_editor):
    from django.apps import apps as django_apps
    from django.contrib.auth.management import create_permissions

    Plano = apps.get_model("assinatura", "Plano")
    for slug, nome, valor, intervalo, qtd in PLANOS:
        # get_or_create: reaplicar nunca sobrescreve um valor que o administrador já tenha alterado.
        Plano.objects.get_or_create(slug=slug, defaults={"nome": nome, "valor": valor, "intervalo": intervalo, "intervalo_qtd": qtd})
    create_permissions(django_apps.get_app_config("assinatura"), apps=apps, verbosity=0)
    Permission, Group = apps.get_model("auth", "Permission"), apps.get_model("auth", "Group")
    todas = Permission.objects.filter(content_type__app_label="assinatura")
    for grupo in Group.objects.filter(name="Administrador"):
        grupo.permissions.add(*todas)
    gestor = todas.filter(codename__in=("view_assinatura", "gerenciar_assinatura"))
    for grupo in Group.objects.filter(name="Gestor"):
        grupo.permissions.add(*gestor)


class Migration(migrations.Migration):
    dependencies = [("assinatura", "0001_initial")]
    operations = [migrations.RunPython(aplicar, migrations.RunPython.noop)]
