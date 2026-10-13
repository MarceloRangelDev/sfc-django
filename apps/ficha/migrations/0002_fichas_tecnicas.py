"""Transforma a âncora de permissões `Ficha` em cadastro real.

`cliente` e `produto` são obrigatórios e linhas antigas (se existirem) não têm
como receber valores corretos: a tela antiga nunca gravou registros; se houver
algum, a migration PARA com mensagem clara em vez de inventar dados.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def exigir_tabela_vazia(apps, schema_editor):
    if apps.get_model("ficha", "Ficha").objects.exists():
        raise RuntimeError(
            "Existem Fichas antigas sem cliente/produto. Associe-as (ou remova-as) manualmente "
            "antes de aplicar esta migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0008_permissoes_itens_filiais"),
        ("ficha", "0001_initial"),
        ("insumo", "0006_valor_pago_rotulo"),
        ("medida", "0001_initial"),
        ("produto", "0002_cadastro_de_produtos"),
    ]

    operations = [
        migrations.AddField("ficha", "cliente", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="fichas", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AddField("ficha", "produto", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="fichas", to="produto.produto", verbose_name="Produto")),
        migrations.RunPython(exigir_tabela_vazia, migrations.RunPython.noop),
        migrations.AlterField("ficha", "cliente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fichas", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AlterField("ficha", "produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fichas", to="produto.produto", verbose_name="Produto")),
        migrations.AlterField("ficha", "nome", models.CharField(max_length=150, verbose_name="Nome da ficha")),
        migrations.AddField("ficha", "custo_total", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo total (centavos)")),
        migrations.AddField("ficha", "ativo", models.BooleanField(default=True, verbose_name="Ativo")),
        migrations.AlterModelOptions("ficha", {"ordering": ["nome"], "verbose_name": "Ficha Técnica", "verbose_name_plural": "Fichas Técnicas"}),
        migrations.AlterUniqueTogether("ficha", {("cliente", "nome")}),
        migrations.AddIndex("ficha", models.Index(fields=["cliente", "ativo"], name="ficha_cliente_ativo_idx")),
        migrations.CreateModel(
            name="FichaItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantidade", models.DecimalField(decimal_places=4, max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")])),
                ("custo", models.PositiveBigIntegerField(default=0, editable=False)),
                ("ficha", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="ficha.ficha")),
                ("insumo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usos_em_fichas", to="insumo.insumo")),
                ("medida", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="itens_de_ficha", to="medida.medida")),
            ],
            options={"verbose_name": "Item da ficha", "verbose_name_plural": "Itens da ficha", "ordering": ["id"]},
        ),
        migrations.AddConstraint("fichaitem", models.UniqueConstraint(fields=("ficha", "insumo"), name="ficha_item_insumo_unico")),
    ]
