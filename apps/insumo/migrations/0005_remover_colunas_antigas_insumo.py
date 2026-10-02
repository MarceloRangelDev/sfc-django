"""Passo 3/3: remove as colunas antigas (preco, unidade_medida), já copiadas
e conferidas no passo 2, e torna `medida` obrigatória.

Reversível: o `default` temporário em `preco` permite que o reverso recrie a
coluna mesmo com linhas existentes; o passo 2 reverso então a preenche.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("insumo", "0004_copiar_dados_insumo")]

    operations = [
        migrations.AlterField(
            model_name="insumo",
            name="preco",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=15, verbose_name="Preço pago (R$)"),
        ),
        migrations.RemoveField(model_name="insumo", name="preco"),
        migrations.RemoveField(model_name="insumo", name="unidade_medida"),
        migrations.AlterField(
            model_name="insumo",
            name="medida",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="insumos",
                to="medida.medida", verbose_name="Unidade de medida",
            ),
        ),
        migrations.AlterField(
            model_name="insumo",
            name="quantidade",
            field=models.DecimalField(
                decimal_places=4, max_digits=15, verbose_name="Quantidade comprada",
                validators=[django.core.validators.MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
            ),
        ),
        migrations.AddIndex(
            model_name="insumo",
            index=models.Index(fields=["cliente", "ativo"], name="insumo_cliente_ativo_idx"),
        ),
    ]
