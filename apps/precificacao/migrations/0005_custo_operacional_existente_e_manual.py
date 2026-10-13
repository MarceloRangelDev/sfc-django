"""Precificações já gravadas: o custo operacional foi DIGITADO pelo usuário, então valores diferentes
de zero passam a valer como "ajustados manualmente" e nunca são sobrescritos pela distribuição
automática. Valor zero segue a distribuição. Reversível (o rollback não precisa desfazer nada)."""
from django.db import migrations


def marcar(apps, schema_editor):
    Precificacao = apps.get_model("precificacao", "Precificacao")
    Precificacao.objects.filter(custo_operacional__gt=0).update(custo_operacional_manual=True)


class Migration(migrations.Migration):
    dependencies = [("precificacao", "0004_custo_operacional_manual")]
    operations = [migrations.RunPython(marcar, migrations.RunPython.noop)]
