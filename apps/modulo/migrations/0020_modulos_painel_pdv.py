"""Painel e as telas do PDV viram módulos do catálogo (Módulos, Papéis e Permissões especiais).

Nada muda para quem já usa: cada módulo novo entra nos segmentos e nos clientes que já tinham o módulo-pai (PDV) — o Painel entra em todos —,
e as permissões novas foram concedidas, nas migrações do pdv/dashboard, aos papéis que já tinham a permissão equivalente.
O módulo "pdv" deixa de ter item de menu próprio (as telas aparecem agrupadas em "PDV")."""
from django.db import migrations

from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO, instrucoes_padrao

FILHOS_PDV = ("pdv-vender", "pdv-comandas", "pdv-caixa", "pdv-vendas", "pdv-caixas")


def carregar(apps, schema_editor):
    Modulo, Segmento, ClienteModulo = (apps.get_model("modulo", n) for n in ("Modulo", "Segmento", "ClienteModulo"))
    Cliente = apps.get_model("cliente", "Cliente")
    for slug in ("painel", "pdv", *FILHOS_PDV):
        campos = {"essencial": False, "delegavel": True, **MODULOS_PADRAO[slug]}
        Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True, "instrucoes": instrucoes_padrao(slug, campos)})
    pai = Modulo.objects.get(slug="pdv")
    for slug in ("painel", *FILHOS_PDV):
        modulo = Modulo.objects.get(slug=slug)
        # Segmentos: o Painel em todos; as telas do PDV onde o PDV já está.
        for segmento in Segmento.objects.all():
            if slug == "painel" or segmento.modulos.filter(pk=pai.pk).exists():
                segmento.modulos.add(modulo)
        # Clientes: o Painel para todos com segmento; as telas do PDV onde o PDV está habilitado (respeita quem o desabilitou).
        for cliente in Cliente.objects.filter(segmento__isnull=False):
            if slug == "painel":
                ativo = True
            else:
                vinculo = ClienteModulo.objects.filter(cliente=cliente, modulo=pai).first()
                if vinculo is None:
                    continue
                ativo = vinculo.ativo
            ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": ativo})


class Migration(migrations.Migration):
    dependencies = [("modulo", "0019_modulo_imagens"), ("pdv", "0004_alter_caixa_options_alter_venda_options"), ("dashboard", "0002_alter_presenca_options"), ("cliente", "0009_fiscal_cadastro")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
