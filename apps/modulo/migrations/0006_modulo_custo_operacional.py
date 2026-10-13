"""Acrescenta ao catálogo o módulo de negócio "Custo operacional".

Módulo de negócio depende do segmento e da habilitação por cliente (cadeia
Usuário → Cliente → Segmento → Módulo). Para bancos existentes, a migration:
- cadastra o módulo;
- inclui o módulo nos segmentos padrão (como a 0003 fez com os demais);
- habilita o módulo para os clientes que JÁ têm segmento (mesma regra do
  comando `seed_modulos`: nunca sobrescreve um vínculo existente, então quem
  foi desabilitado por um administrador continua desabilitado).
"""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, SEGMENTOS_PADRAO, instrucoes_padrao

SLUG = "custo-operacional"


def carregar(apps, schema_editor):
    Modulo = apps.get_model("modulo", "Modulo")
    Segmento = apps.get_model("modulo", "Segmento")
    Cliente = apps.get_model("cliente", "Cliente")
    ClienteModulo = apps.get_model("modulo", "ClienteModulo")

    campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[SLUG]}
    modulo, _ = Modulo.objects.update_or_create(
        slug=SLUG, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(SLUG, campos)}
    )
    for slug in SEGMENTOS_PADRAO:
        segmento = Segmento.objects.filter(slug=slug).first()
        if segmento:
            segmento.modulos.add(modulo)
    for cliente in Cliente.objects.filter(segmento__isnull=False):
        ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0008_permissoes_itens_filiais"),
        ("modulo", "0005_modulo_canal_venda"),
    ]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
