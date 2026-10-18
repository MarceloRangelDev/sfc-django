"""Acrescenta ao catálogo (Configurações) "Imagens e custos (R2)": exclusivo do proprietário da plataforma, não delegável."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO["imagens"]}
    Modulo.objects.update_or_create(slug="imagens", defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao("imagens", campos)})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0018_modulos_fiscal")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
