"""Acrescenta ao catálogo "Agenda" (menu principal) e "Integrações" (Configurações). Os dois são essenciais: só dependem da permissão do
perfil (conexões e mensagens ficam com Gestor/Administrador; a agenda é de quem opera)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUGS = ("agenda", "integracoes")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug in SLUGS:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0016_modulo_mao_de_obra")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
