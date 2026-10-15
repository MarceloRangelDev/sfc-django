"""Acrescenta ao catálogo o módulo "Mão de obra" (negócio) SOMENTE aos segmentos que precificam serviço
(Serviço e Produto e Serviço) e aos clientes desses segmentos; produto puro não recebe o módulo."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUG = "mao-de-obra"
TIPOS = ("servico", "ambos")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[SLUG]}
    modulo, _ = Modulo.objects.update_or_create(slug=SLUG, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(SLUG, campos)})
    for segmento in Segmento.objects.filter(slug__in=("servico", "produto", "ambos"), tipo_precificacao__in=TIPOS):
        segmento.modulos.add(modulo)
    for cliente in Cliente.objects.filter(segmento__tipo_precificacao__in=TIPOS):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0015_modulo_pedido"), ("cliente", "0008_permissoes_itens_filiais")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
