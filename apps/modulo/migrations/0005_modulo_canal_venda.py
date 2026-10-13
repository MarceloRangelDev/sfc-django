"""Acrescenta ao catálogo o módulo "Canal de vendas" (Configurações)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUG = "canal-venda"


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[SLUG]}
    Modulo.objects.update_or_create(
        slug=SLUG, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(SLUG, campos)}
    )


class Migration(migrations.Migration):
    dependencies = [("modulo", "0004_modulo_itens_filiais")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
