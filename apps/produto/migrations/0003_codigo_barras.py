"""Código de barras/QR por produto: coluna nova (db_default) e geração do código interno para os produtos já cadastrados."""
from django.db import migrations, models


def gerar_codigos(apps, schema_editor):
    from apps.produto.codigos import codigo_interno

    Produto = apps.get_model("produto", "Produto")
    for produto in Produto.objects.filter(codigo_barras=""):
        Produto.objects.filter(pk=produto.pk).update(codigo_barras=codigo_interno(produto.pk))


class Migration(migrations.Migration):
    dependencies = [("produto", "0002_cadastro_de_produtos")]

    operations = [
        migrations.AddField(
            model_name="produto", name="codigo_barras",
            field=models.CharField(blank=True, db_default="", default="", max_length=32, verbose_name="Código de barras"),
        ),
        migrations.RunPython(gerar_codigos, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="produto",
            constraint=models.UniqueConstraint(
                condition=models.Q(("codigo_barras", ""), _negated=True), fields=("cliente", "codigo_barras"), name="produto_codigo_unico_por_unidade",
            ),
        ),
    ]
