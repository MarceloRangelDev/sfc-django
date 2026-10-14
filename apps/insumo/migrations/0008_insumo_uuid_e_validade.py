"""Passo 1 de 3 do identificador público do insumo (QR Code): cria `uuid` ainda NULO e sem unicidade
(para as linhas existentes receberem valores distintos no passo 2) e `dias_validade`.
Estrutura e dados ficam em migrações separadas para funcionar em SQLite e PostgreSQL."""
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("insumo", "0007_insumo_marca")]
    operations = [
        migrations.AddField(model_name="insumo", name="uuid", field=models.UUIDField(null=True, editable=False, verbose_name="Identificador (QR Code)")),
        migrations.AddField(
            model_name="insumo", name="dias_validade",
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Validade padrão (dias)"),
        ),
    ]
