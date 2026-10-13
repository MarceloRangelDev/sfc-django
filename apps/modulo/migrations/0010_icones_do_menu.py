"""Ícones do menu que representam o nome de cada módulo (antes todos usavam o ícone genérico "app-window").

Só troca o ícone que ainda é o genérico: se um administrador já escolheu outro pela tela de Módulos, a
escolha dele é mantida. Reversível (volta ao ícone genérico apenas onde este arquivo o trocou)."""
from django.db import migrations

GENERICO = "ti tabler-app-window"
NOVOS = {
    "insumo": "ti tabler-basket",
    "processado": "ti tabler-chef-hat",
    "categoria": "ti tabler-tags",
    "produto": "ti tabler-package",
    "ficha": "ti tabler-clipboard-list",
    "precificacao": "ti tabler-calculator",
}


def trocar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug, icone in NOVOS.items():
        Modulo.objects.filter(slug=slug, icone=GENERICO).update(icone=icone)


def voltar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    for slug, icone in NOVOS.items():
        Modulo.objects.filter(slug=slug, icone=icone).update(icone=GENERICO)


class Migration(migrations.Migration):
    dependencies = [("modulo", "0009_modulo_custo_comum")]
    operations = [migrations.RunPython(trocar, voltar)]
