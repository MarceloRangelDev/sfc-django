"""Carrega o catálogo GLOBAL de custos comuns (água, energia...). Fica em uma
única tabela compartilhada: nada é copiado para os clientes. Idempotente
(`get_or_create` por nome): rodar de novo não duplica nem sobrescreve edições
feitas pelo admin."""
from django.db import migrations

CUSTOS_COMUNS = [
    # (nome, tipo, descrição, ordem)
    ("Aluguel", "fixo", "Aluguel do imóvel.", 10),
    ("Condomínio", "fixo", "Taxa de condomínio.", 20),
    ("Água", "variavel", "Conta de água e esgoto.", 30),
    ("Energia elétrica", "variavel", "Conta de luz.", 40),
    ("Gás", "variavel", "Gás de cozinha/encanado.", 50),
    ("Internet", "fixo", "Plano de internet.", 60),
    ("Telefone", "fixo", "Telefonia fixa e móvel.", 70),
    ("Contabilidade", "fixo", "Honorários do contador.", 80),
    ("Sistemas e assinaturas", "fixo", "Software, aplicativos e assinaturas.", 90),
    ("Material de limpeza", "variavel", "Produtos de limpeza e higiene.", 100),
]


def carregar(apps, schema_editor):
    CustoModelo = apps.get_model("custo", "CustoModelo")
    for nome, tipo, descricao, ordem in CUSTOS_COMUNS:
        CustoModelo.objects.get_or_create(nome=nome, defaults={"tipo": tipo, "descricao": descricao, "ordem": ordem})


class Migration(migrations.Migration):
    dependencies = [("custo", "0001_custos_operacionais")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
