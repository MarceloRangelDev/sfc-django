"""Acrescenta ao catálogo (Configurações): Insumos globais, Insumos fora do padrão e Foto do perfil. Todos essenciais (só dependem da permissão)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUGS = ("insumo-global", "insumo-nao-conformes", "perfil-foto")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug in SLUGS:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0020_modulos_painel_pdv"), ("insumoglobal", "0002_permissoes_administrador"), ("accounts", "0006_alter_profile_options")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
