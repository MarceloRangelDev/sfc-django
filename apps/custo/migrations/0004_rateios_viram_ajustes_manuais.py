"""Converte as distribuições por PERCENTUAL já cadastradas (CustoRateio) em ajustes manuais
em R$ (CustoAjusteCategoria), para que ninguém perca a decisão que tomou.

Para cada unidade com rateios: valor da categoria = soma, nos custos ATIVOS, de
valor × percentual ÷ 100 (arredondado uma vez no total da categoria, ROUND_HALF_UP).
Só entram categorias com valor > 0. A tabela CustoRateio é mantida (sem uso) e o rollback
apenas apaga os ajustes criados — nada é destruído. Idempotente: não recria ajustes existentes."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations


def converter(apps, schema_editor):
    CustoRateio = apps.get_model("custo", "CustoRateio")
    Ajuste = apps.get_model("custo", "CustoAjusteCategoria")
    acumulado = {}  # (cliente_id, categoria_id) -> Decimal em centavos
    for r in CustoRateio.objects.filter(custo__ativo=True).select_related("custo"):
        chave = (r.custo.cliente_id, r.categoria_id)
        acumulado[chave] = acumulado.get(chave, Decimal(0)) + Decimal(r.custo.valor) * r.percentual / Decimal(100)
    for (cliente_id, categoria_id), valor in acumulado.items():
        centavos = int(valor.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if centavos > 0:
            Ajuste.objects.get_or_create(cliente_id=cliente_id, categoria_id=categoria_id, defaults={"valor": centavos})


def desfazer(apps, schema_editor):
    apps.get_model("custo", "CustoAjusteCategoria").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("custo", "0003_custos_comuns_por_cliente_e_ajuste_manual")]
    operations = [migrations.RunPython(converter, desfazer)]
