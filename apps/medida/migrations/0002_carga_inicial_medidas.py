"""Carga inicial da tabela `medidas` (PROMPT_MESTRE, seção 24).

As 17 medidas são criadas NESTA ORDEM (o id segue a ordem). É idempotente:
usa get_or_create pelo nome, então rodar de novo não duplica nada nem
sobrescreve ajustes feitos depois por um administrador.

Valores de referência de cozinha (editáveis na tela de Medidas):
colher de chá = 5 ml, colher de sopa = 15 ml, colher de café = 2 ml,
copo americano = 200 ml, xícara = 240 ml.
"""
from decimal import Decimal

from django.db import migrations

# (medida, sigla, dimensao, fator_base, equivale, singular, plural, descricao)
MEDIDAS = [
    ("Gramas", "g", "massa", "1", "1000 g = 1 Kg", "grama", "gramas", "Unidade base de massa."),
    ("Quilo", "kg", "massa", "1000", "1 Kg = 1000 g", "quilo", "quilos", "Quilograma."),
    ("Unidade", "un", "unidade", "1", "1 un = 1 item", "unidade", "unidades", "Item individual (ovo, pão, lata...)."),
    ("Mililitro", "ml", "volume", "1", "1000 ml = 1 L", "mililitro", "mililitros", "Unidade base de volume."),
    ("Litro", "L", "volume", "1000", "1 L = 1000 ml", "litro", "litros", "Litro."),
    ("Colher de chá", "c. chá", "volume", "5", "1 colher de chá = 5 ml", "colher de chá", "colheres de chá", "Medida caseira."),
    ("Colher de sopa", "c. sopa", "volume", "15", "1 colher de sopa = 15 ml", "colher de sopa", "colheres de sopa", "Medida caseira."),
    ("Colher de café", "c. café", "volume", "2", "1 colher de café = 2 ml", "colher de café", "colheres de café", "Medida caseira."),
    ("Copo americano", "copo", "volume", "200", "1 copo americano = 200 ml", "copo americano", "copos americanos", "Medida caseira."),
    ("Xícara", "xíc.", "volume", "240", "1 xícara = 240 ml", "xícara", "xícaras", "Xícara de chá (medida caseira)."),
    ("Polegadas", "pol", "comprimento", "0.0254", "1 pol = 2,54 cm", "polegada", "polegadas", "Comprimento (base: metro)."),
    ("Centímetro", "cm", "comprimento", "0.01", "100 cm = 1 m", "centímetro", "centímetros", "Comprimento (base: metro)."),
    ("Metro", "m", "comprimento", "1", "1 m = 100 cm", "metro", "metros", "Unidade base de comprimento."),
    ("Metro quadrado", "m²", "area", "1", "1 m² = 10.000 cm²", "metro quadrado", "metros quadrados", "Área."),
    ("Metro Cúbico", "m³", "volume", "1000000", "1 m³ = 1000 L", "metro cúbico", "metros cúbicos", "Volume (1 m³ = 1.000.000 ml)."),
    ("Segundos", "s", "tempo", "1", "60 s = 1 min", "segundo", "segundos", "Unidade base de tempo."),
    ("Quilômetro", "km", "comprimento", "1000", "1 km = 1000 m", "quilômetro", "quilômetros", "Comprimento (base: metro)."),
]


def carregar_medidas(apps, schema_editor):
    Medida = apps.get_model("medida", "Medida")
    for medida, sigla, dimensao, fator, equivale, singular, plural, descricao in MEDIDAS:
        Medida.objects.get_or_create(
            medida=medida,
            defaults={
                "sigla": sigla,
                "dimensao": dimensao,
                "fator_base": Decimal(fator),
                "equivale": equivale,
                "nome_singular": singular,
                "nome_plural": plural,
                "descricao": descricao,
                "ativo": True,
            },
        )


def remover_medidas(apps, schema_editor):
    # Reversível, mas só apaga as medidas ainda não usadas por ninguém.
    Medida = apps.get_model("medida", "Medida")
    Medida.objects.filter(medida__in=[m[0] for m in MEDIDAS]).delete()


class Migration(migrations.Migration):
    dependencies = [("medida", "0001_initial")]
    operations = [migrations.RunPython(carregar_medidas, remover_medidas)]
