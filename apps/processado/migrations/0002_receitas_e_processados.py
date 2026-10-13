"""Transforma a âncora de permissões `Processado` em cadastro real.

`cliente` e `rendimento_medida` são obrigatórios, mas as linhas antigas (se
existirem) não têm como receber um valor correto. A tela antiga nunca gravou
registros; se houver algum (ex.: criado pelo admin), a migration PARA com uma
mensagem clara em vez de inventar dono ou apagar dados.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def exigir_tabela_vazia(apps, schema_editor):
    if apps.get_model("processado", "Processado").objects.exists():
        raise RuntimeError(
            "Existem Processados antigos sem cliente/unidade de saída. Associe-os (ou remova-os) "
            "manualmente antes de aplicar esta migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0008_permissoes_itens_filiais"),
        ("insumo", "0006_valor_pago_rotulo"),
        ("medida", "0001_initial"),
        ("processado", "0001_initial"),
    ]

    operations = [
        migrations.AddField("processado", "cliente", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="processados", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AddField("processado", "rendimento_medida", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="processados_rendimento", to="medida.medida", verbose_name="Unidade de saída")),
        migrations.RunPython(exigir_tabela_vazia, migrations.RunPython.noop),
        migrations.AlterField("processado", "cliente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="processados", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AlterField("processado", "rendimento_medida", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="processados_rendimento", to="medida.medida", verbose_name="Unidade de saída")),
        migrations.AddField("processado", "rendimento_quantidade", models.DecimalField(decimal_places=4, default=Decimal("1"), max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal("0.0001"), message="O rendimento deve ser maior que zero.")], verbose_name="Rendimento")),
        migrations.AddField("processado", "custo_total", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo total (centavos)")),
        migrations.AddField("processado", "fator_rendimento", models.DecimalField(blank=True, decimal_places=4, editable=False, max_digits=12, null=True, verbose_name="Fator de rendimento")),
        migrations.AddField("processado", "insumo", models.OneToOneField(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="processado_origem", to="insumo.insumo", verbose_name="Insumo gerado")),
        migrations.AddField("processado", "ativo", models.BooleanField(default=True, verbose_name="Ativo")),
        migrations.AlterField("processado", "nome", models.CharField(max_length=150, verbose_name="Nome")),
        migrations.AlterModelOptions("processado", {"ordering": ["nome"], "verbose_name": "Processado", "verbose_name_plural": "Processados"}),
        migrations.AlterUniqueTogether("processado", {("cliente", "nome")}),
        migrations.AddIndex("processado", models.Index(fields=["cliente", "ativo"], name="processado_cliente_ativo_idx")),
        migrations.CreateModel(
            name="ProcessadoItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantidade", models.DecimalField(decimal_places=4, max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")])),
                ("custo", models.PositiveBigIntegerField(default=0, editable=False)),
                ("insumo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usos_em_processados", to="insumo.insumo")),
                ("medida", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="itens_de_processado", to="medida.medida")),
                ("processado", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="processado.processado")),
            ],
            options={"verbose_name": "Item do processado", "verbose_name_plural": "Itens do processado", "ordering": ["id"]},
        ),
        migrations.AddConstraint("processadoitem", models.UniqueConstraint(fields=("processado", "insumo"), name="processado_item_insumo_unico")),
    ]
