"""Acrescenta ao catálogo (Configurações) o módulo Kanban (essencial: depende só da permissão, concedida ao Administrador)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUGS = ("kanban",)


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug in SLUGS:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0021_modulos_insumo_global_foto"), ("kanban", "0002_permissoes_administrador")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
