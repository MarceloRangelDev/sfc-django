"""Transforma a âncora de permissões `Precificacao` em cadastro real.

`cliente`, `ficha` e `produto` são obrigatórios e linhas antigas (se existirem)
não têm como receber valores corretos: a tela antiga nunca gravou registros; se
houver algum, a migration PARA com mensagem clara em vez de inventar dados.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def exigir_tabela_vazia(apps, schema_editor):
    if apps.get_model("precificacao", "Precificacao").objects.exists():
        raise RuntimeError(
            "Existem Precificações antigas sem cliente/ficha/produto. Associe-as (ou remova-as) "
            "manualmente antes de aplicar esta migration."
        )



class Migration(migrations.Migration):
    dependencies = [
        ("canal", "0001_canais_de_venda"),
        ("cliente", "0008_permissoes_itens_filiais"),
        ("ficha", "0002_fichas_tecnicas"),
        ("precificacao", "0001_initial"),
        ("produto", "0002_cadastro_de_produtos"),
    ]

    operations = [
        # Obrigatórios: entram anuláveis, a tabela é verificada e depois viram NOT NULL.
        migrations.AddField("precificacao", "cliente", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AddField("precificacao", "ficha", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="ficha.ficha", verbose_name="Ficha técnica")),
        migrations.AddField("precificacao", "produto", models.ForeignKey(null=True, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="produto.produto", verbose_name="Produto")),
        migrations.RunPython(exigir_tabela_vazia, migrations.RunPython.noop),
        migrations.AlterField("precificacao", "cliente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="cliente.cliente", verbose_name="Cliente")),
        migrations.AlterField("precificacao", "ficha", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="ficha.ficha", verbose_name="Ficha técnica")),
        migrations.AlterField("precificacao", "produto", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="produto.produto", verbose_name="Produto")),
        migrations.AlterField("precificacao", "nome", models.CharField(max_length=150, verbose_name="Nome da precificação")),
        migrations.AddField("precificacao", "canal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="precificacoes", to="canal.canalvenda", verbose_name="Canal de venda")),
        migrations.AddField("precificacao", "canal_nome", models.CharField(blank=True, max_length=150, verbose_name="Nome do canal")),
        migrations.AddField("precificacao", "canal_descricao", models.CharField(blank=True, max_length=255, verbose_name="Descrição do canal")),
        migrations.AddField("precificacao", "taxa_percentual", models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal("0")), django.core.validators.MaxValueValidator(Decimal("99.9999"))], verbose_name="Taxas do canal (%)")),
        migrations.AddField("precificacao", "taxa_fixa", models.PositiveBigIntegerField(default=0, verbose_name="Taxas fixas do canal (centavos)")),
        migrations.AddField("precificacao", "custo_operacional", models.PositiveBigIntegerField(default=0, verbose_name="Custo operacional (centavos)")),
        migrations.AddField("precificacao", "metodo_lucro", models.CharField(choices=[("margem", "Margem (% sobre o preço de venda)"), ("markup", "Markup (% sobre o custo)"), ("valor", "Valor em reais")], default="margem", max_length=10, verbose_name="Método do lucro")),
        migrations.AddField("precificacao", "percentual_lucro", models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=9, validators=[django.core.validators.MinValueValidator(Decimal("0"))], verbose_name="Margem ou markup (%)")),
        migrations.AddField("precificacao", "valor_lucro", models.PositiveBigIntegerField(default=0, verbose_name="Lucro em reais (centavos)")),
        migrations.AddField("precificacao", "custo_ficha", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo da ficha (centavos)")),
        migrations.AddField("precificacao", "custo_total", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo antes do canal e do lucro (centavos)")),
        migrations.AddField("precificacao", "taxas_percentuais_valor", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Taxas percentuais em reais (centavos)")),
        migrations.AddField("precificacao", "lucro", models.BigIntegerField(default=0, editable=False, verbose_name="Lucro (centavos)")),
        migrations.AddField("precificacao", "preco_venda", models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Preço de venda (centavos)")),
        migrations.AddField("precificacao", "cmv_percentual", models.DecimalField(decimal_places=2, default=Decimal("0"), editable=False, max_digits=9, verbose_name="CMV (%)")),
        migrations.AddField("precificacao", "ativo", models.BooleanField(default=True, verbose_name="Ativo")),
        migrations.AlterModelOptions("precificacao", {"ordering": ["nome"], "verbose_name": "Precificação", "verbose_name_plural": "Precificações"}),
        migrations.AlterUniqueTogether("precificacao", {("cliente", "nome")}),
        migrations.AddIndex("precificacao", models.Index(fields=["cliente", "ativo"], name="precificacao_cliente_ativo_idx")),
    ]
