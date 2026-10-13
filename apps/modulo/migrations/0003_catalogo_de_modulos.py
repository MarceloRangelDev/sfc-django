"""Carrega o catálogo de módulos e os segmentos padrão (mesmos dados do
comando seed_modulos), para que um banco novo já nasça com menu e matriz de
papéis completos."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    negocio = []
    for slug, campos in MODULOS_PADRAO.items():
        campos = {"essencial": False, "delegavel": True, **campos}
        modulo, _ = Modulo.objects.update_or_create(
            slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)}
        )
        if not modulo.essencial:
            negocio.append(modulo)
    for slug, (nome, tipo) in SEGMENTOS_PADRAO.items():
        segmento, _ = Segmento.objects.get_or_create(slug=slug, defaults={"nome": nome, "tipo_precificacao": tipo})
        segmento.modulos.add(*negocio)


class Migration(migrations.Migration):
    dependencies = [("modulo", "0002_segmento_e_campos_de_menu")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
