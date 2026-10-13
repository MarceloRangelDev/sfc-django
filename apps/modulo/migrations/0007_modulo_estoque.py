"""Acrescenta ao catálogo os módulos do Estoque.

- "estoque": módulo de negócio (menu). Como na 0006, entra nos segmentos padrão e é
  habilitado para clientes que JÁ têm segmento, sem sobrescrever vínculos existentes.
- "estoque-transferencia": só uma linha da matriz de papéis (sem menu, essencial) para
  conceder/negar as transferências entre matriz e filiais.
"""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    modulos = {}
    for slug in ("estoque", "estoque-transferencia"):
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        modulos[slug], _ = Modulo.objects.update_or_create(
            slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)}
        )
    negocio = modulos["estoque"]
    for slug in SEGMENTOS_PADRAO:
        segmento = Segmento.objects.filter(slug=slug).first()
        if segmento:
            segmento.modulos.add(negocio)
    for cliente in Cliente.objects.filter(segmento__isnull=False):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=negocio, defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0008_permissoes_itens_filiais"),
        ("modulo", "0006_modulo_custo_operacional"),
    ]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
