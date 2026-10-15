"""Acrescenta ao catálogo o módulo "Pedidos e cozinha" (negócio: segmentos padrão e clientes com segmento)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao

SLUG = "pedido"


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[SLUG]}
    modulo, _ = Modulo.objects.update_or_create(slug=SLUG, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(SLUG, campos)})
    for slug in SEGMENTOS_PADRAO:
        segmento = Segmento.objects.filter(slug=slug).first()
        if segmento:
            segmento.modulos.add(modulo)
    for cliente in Cliente.objects.filter(segmento__isnull=False):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0014_modulos_pdv"), ("cliente", "0008_permissoes_itens_filiais")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
