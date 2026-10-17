"""Tarifas iniciais da ESTIMATIVA, conforme a documentação oficial consultada em 2026-10-17. São apenas ponto de partida: o proprietário
da plataforma as atualiza no painel (com auditoria) quando a Cloudflare mudar preços. Idempotente; não sobrescreve valores já editados."""
import datetime

from django.db import migrations

CONSULTA = datetime.date(2026, 10, 17)
R2 = "https://developers.cloudflare.com/r2/pricing/"
IMAGES = "https://developers.cloudflare.com/images/pricing/"
# chave, preço por unidade de cobrança, franquia mensal (mesma unidade), fonte
TARIFAS = [
    ("armazenamento", "0.015", "10", R2),   # US$ por GB-mês; 10 GB-mês grátis (Standard)
    ("classe_a", "4.50", "1", R2),          # US$ por milhão de operações; 1 milhão grátis
    ("classe_b", "0.36", "10", R2),         # US$ por milhão de operações; 10 milhões grátis
    ("transformacao", "0.50", "5", IMAGES),  # US$ por mil transformações únicas; 5 mil incluídas (plano pago)
]


def semear(apps, schema_editor):
    Tarifa = apps.get_model("imagens", "TarifaImagens")
    for chave, preco, franquia, fonte in TARIFAS:
        Tarifa.objects.get_or_create(chave=chave, defaults={"preco": preco, "franquia": franquia, "moeda": "USD", "fonte": fonte, "consultado_em": CONSULTA})


class Migration(migrations.Migration):
    dependencies = [("imagens", "0001_imagens_r2")]
    operations = [migrations.RunPython(semear, migrations.RunPython.noop)]
