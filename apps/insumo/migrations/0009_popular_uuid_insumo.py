"""Passo 2 de 3: dá um UUID aleatório a cada insumo existente (um por linha; não é derivado do id)."""
import uuid

from django.db import migrations


def popular(apps, schema_editor):
    Insumo = apps.get_model("insumo", "Insumo")
    for pk in Insumo.objects.filter(uuid__isnull=True).values_list("pk", flat=True).iterator():
        Insumo.objects.filter(pk=pk).update(uuid=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("insumo", "0008_insumo_uuid_e_validade")]
    operations = [migrations.RunPython(popular, migrations.RunPython.noop)]
