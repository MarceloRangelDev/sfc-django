"""Acrescenta ao catálogo o módulo "Precificação de serviços" SOMENTE aos segmentos que precificam serviço (Serviço e Serviço e Produto)
e aos clientes desses segmentos; produto puro não recebe o módulo. Não altera módulos nem habilitações existentes."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

SLUG = "precificacao-servico"
TIPOS = ("servico", "ambos")


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[SLUG]}
    modulo, _ = Modulo.objects.update_or_create(slug=SLUG, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(SLUG, campos)})
    for segmento in Segmento.objects.filter(tipo_precificacao__in=TIPOS):
        segmento.modulos.add(modulo)
    # Num banco novo a migração 0003 já ligou TODO o catálogo a todos os segmentos; o módulo é novo nesta migração, então
    # retirá-lo dos segmentos de produto puro só desfaz esse efeito (nenhuma habilitação de cliente existia).
    for segmento in Segmento.objects.exclude(tipo_precificacao__in=TIPOS):
        segmento.modulos.remove(modulo)
    for cliente in Cliente.objects.filter(segmento__tipo_precificacao__in=TIPOS):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0025_ordem_menu_cliente")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
