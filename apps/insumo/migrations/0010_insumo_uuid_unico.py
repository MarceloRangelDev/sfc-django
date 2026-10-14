"""Passo 3 de 3: `uuid` passa a ser obrigatório, único e com valor automático nos novos insumos."""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("insumo", "0009_popular_uuid_insumo")]
    operations = [
        migrations.AlterField(model_name="insumo", name="uuid", field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="Identificador (QR Code)")),
    ]
