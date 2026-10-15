"""Acrescenta ao catálogo "Notas fiscais" (menu principal) e "Configuração fiscal" (Configurações); ambos essenciais (só dependem da permissão)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUGS = ("fiscal", "fiscal-config")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug in SLUGS:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0017_modulos_integracao")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
