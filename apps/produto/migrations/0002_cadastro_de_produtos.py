"""Transforma a âncora de permissões `Produto` em cadastro real.

`cliente` e `categoria` são obrigatórios, mas linhas antigas (se existirem) não
têm como receber valores corretos. A tela antiga nunca gravou registros; se
houver algum (ex.: criado pelo admin), a migration PARA com mensagem clara em
vez de inventar dono/categoria ou apagar dados.
"""
import django.db.models.deletion
from django.db import migrations, models


def exigir_tabela_vazia(apps, schema_editor):
    if apps.get_model("produto", "Produto").objects.exists():
        raise RuntimeError(
            "Existem Produtos antigos sem cliente/categoria. Associe-os (ou remova-os) manualmente "
            "antes de aplicar esta migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("categoria", "0003_ativo_e_dados_de_venda"),
        ("cliente", "0008_permissoes_itens_filiais"),
        ("produto", "0001_initial"),
    ]

    operations = [
        migrations.AddField("produto", "cliente", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="produtos", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AddField("produto", "categoria", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="produtos", to="categoria.categoria", verbose_name="Categoria")),
        migrations.RunPython(exigir_tabela_vazia, migrations.RunPython.noop),
        migrations.AlterField("produto", "cliente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="produtos", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AlterField("produto", "categoria", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="produtos", to="categoria.categoria", verbose_name="Categoria")),
        migrations.AddField("produto", "descricao", models.CharField(blank=True, max_length=255, verbose_name="Descrição")),
        migrations.AddField("produto", "ativo", models.BooleanField(default=True, verbose_name="Ativo")),
        migrations.AlterField("produto", "nome", models.CharField(max_length=150, verbose_name="Nome")),
        migrations.AlterModelOptions("produto", {"ordering": ["nome"], "verbose_name": "Produto", "verbose_name_plural": "Produtos"}),
        migrations.AlterUniqueTogether("produto", {("cliente", "nome")}),
        migrations.AddIndex("produto", models.Index(fields=["cliente", "ativo"], name="produto_cliente_ativo_idx")),
    ]
