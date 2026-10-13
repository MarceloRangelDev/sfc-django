"""Acrescenta ao catálogo os módulos do Financeiro.

- "financeiro-pagar", "financeiro-receber" e "financeiro-relatorios" (DRE e balanço) são
  módulos de negócio (menu): entram nos segmentos padrão e são habilitados para clientes
  que JÁ têm segmento, sem sobrescrever vínculos existentes (como nas 0006/0007).
- "financeiro-categorias" é só uma linha da matriz de papéis (sem menu, essencial).
"""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao

NEGOCIO = ("financeiro-pagar", "financeiro-receber", "financeiro-relatorios")
SOMENTE_MATRIZ = ("financeiro-categorias",)


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    criados = {}
    for slug in NEGOCIO + SOMENTE_MATRIZ:
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        criados[slug], _ = Modulo.objects.update_or_create(
            slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)}
        )
    for slug in SEGMENTOS_PADRAO:
        segmento = Segmento.objects.filter(slug=slug).first()
        if segmento:
            segmento.modulos.add(*(criados[s] for s in NEGOCIO))
    for cliente in Cliente.objects.filter(segmento__isnull=False):
        for slug in NEGOCIO:
            ClienteModulo.objects.get_or_create(cliente=cliente, modulo=criados[slug], defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0008_permissoes_itens_filiais"),
        ("modulo", "0007_modulo_estoque"),
    ]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
