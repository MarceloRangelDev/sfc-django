"""Acrescenta ao catálogo o módulo "PDV" (negócio: segmentos padrão e clientes com segmento) e os três módulos de
Configurações do PDV (formas de pagamento, mesas e ajustes; essenciais: só dependem da permissão do perfil)."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao

NEGOCIO = "pdv"
CONFIG = ("forma-pagamento", "mesa", "pdv-config")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    modulos = {}
    for slug in (NEGOCIO, *CONFIG):
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        modulos[slug], _ = Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})
    for slug in SEGMENTOS_PADRAO:
        segmento = Segmento.objects.filter(slug=slug).first()
        if segmento:
            segmento.modulos.add(modulos[NEGOCIO])
    for cliente in Cliente.objects.filter(segmento__isnull=False):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulos[NEGOCIO], defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0013_modulo_cotacao"), ("cliente", "0008_permissoes_itens_filiais")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
