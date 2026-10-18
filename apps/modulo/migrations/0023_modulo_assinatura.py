"""Acrescenta ao catálogo (Configurações) o módulo Assinatura (essencial: depende só da permissão)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUGS = ("assinatura",)


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug in SLUGS:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})


class Migration(migrations.Migration):
    dependencies = [("assinatura", "0002_planos_e_permissoes"), ("modulo", "0022_modulo_kanban")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
