"""Clientes que já existiam continuam podendo usar TODOS os perfis atuais
(comportamento anterior, em que os papéis eram globais). Só clientes criados
depois começam sem perfis e recebem os desejados na tela de Clientes."""
from django.db import migrations


def liberar_perfis_existentes(apps, schema_editor):
    Cliente = apps.get_model("cliente", "Cliente")
    Group = apps.get_model("auth", "Group")
    grupos = list(Group.objects.all())
    for cliente in Cliente.objects.all():
        cliente.perfis.add(*grupos)


class Migration(migrations.Migration):
    dependencies = [("cliente", "0003_cliente_perfis")]
    operations = [migrations.RunPython(liberar_perfis_existentes, migrations.RunPython.noop)]
