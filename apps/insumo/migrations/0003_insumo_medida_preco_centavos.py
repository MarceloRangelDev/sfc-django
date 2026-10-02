"""Passo 1/3 da migração do Insumo para medidas + centavos (schema aditivo).

Só ADICIONA colunas novas (medida, preco_pago, preco_unidade) e torna a
categoria opcional; nada é removido ainda. A cópia dos dados é o passo 2
(0004) e a remoção das colunas antigas é o passo 3 (0005). Separar em três
migrations evita misturar alteração de estrutura e de dados na mesma
transação (problema conhecido no PostgreSQL) e permite validar a conversão
antes de apagar o formato antigo.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("categoria", "0002_alter_categoria_options_categoria_cliente_and_more"),
        ("medida", "0002_carga_inicial_medidas"),
        ("insumo", "0002_alter_insumo_options_insumo_ativo_insumo_categoria_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="insumo",
            name="categoria",
            field=models.ForeignKey(
                blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="insumos", to="categoria.categoria", verbose_name="Categoria",
            ),
        ),
        migrations.AddField(
            model_name="insumo",
            name="medida",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="insumos",
                to="medida.medida", verbose_name="Unidade de medida",
            ),
        ),
        migrations.AddField(
            model_name="insumo",
            name="preco_pago",
            field=models.PositiveBigIntegerField(default=0, verbose_name="Valor pago (centavos)"),
        ),
        migrations.AddField(
            model_name="insumo",
            name="preco_unidade",
            field=models.PositiveBigIntegerField(
                blank=True, editable=False, null=True, verbose_name="Preço por unidade (centavos)"
            ),
        ),
    ]
