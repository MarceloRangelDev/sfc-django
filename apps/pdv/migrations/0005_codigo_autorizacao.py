from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pdv", "0004_alter_caixa_options_alter_venda_options"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CodigoAutorizacao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("usuario", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="codigo_autorizacao", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Código de autorização", "verbose_name_plural": "Códigos de autorização"},
        ),
    ]
