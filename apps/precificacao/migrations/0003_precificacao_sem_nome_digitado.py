"""A precificação passa a ser identificada pela ficha técnica (produto) e pelo canal,
não por um nome digitado: `nome` vira um rótulo GERADO ("Ficha — Canal") e deixa de
ser único (o mesmo produto pode ter várias precificações). Os nomes já gravados são
substituídos pelo rótulo gerado (a migration regrava os existentes)."""
from django.db import migrations, models


def gerar_rotulos(apps, schema_editor):
    """Troca o nome digitado pelo rótulo gerado nas precificações existentes
    (mesma regra de services.preencher)."""
    Precificacao = apps.get_model("precificacao", "Precificacao")
    for p in Precificacao.objects.select_related("ficha"):
        canal = f"{p.canal_nome} | {p.canal_descricao}" if p.canal_nome and p.canal_descricao else p.canal_nome
        Precificacao.objects.filter(pk=p.pk).update(nome=(f"{p.ficha.nome} — {canal}" if canal else p.ficha.nome)[:150])


class Migration(migrations.Migration):
    dependencies = [("precificacao", "0002_precificacoes")]

    operations = [
        migrations.AlterUniqueTogether("precificacao", set()),
        migrations.AlterModelOptions("precificacao", {"ordering": ["nome", "pk"], "verbose_name": "Precificação", "verbose_name_plural": "Precificações"}),
        migrations.AlterField("precificacao", "nome", models.CharField(blank=True, editable=False, max_length=150, verbose_name="Identificação")),
        migrations.RunPython(gerar_rotulos, migrations.RunPython.noop),
    ]
