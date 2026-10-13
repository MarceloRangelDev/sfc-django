"""Move o "segmento de trabalho" do usuário (Profile) para o Cliente.

Usa os segmentos padrão (Serviço, Produto, Serviço e Produto), aponta cada cliente para o segmento escolhido pelo
seu primeiro usuário e marca o cliente do sistema (dono do perfil Admin).
"""
from django.db import migrations

NOMES = {"servico": "Serviço", "produto": "Produto", "ambos": "Serviço e Produto"}
NOME_SISTEMA = "SISTEMA FATURANDO CERTO SERVIÇOS LTDA"


def migrar(apps, schema_editor):
    Cliente = apps.get_model("cliente", "Cliente")
    Profile = apps.get_model("accounts", "Profile")
    Segmento = apps.get_model("modulo", "Segmento")

    segmentos = {}
    for tipo, nome in NOMES.items():
        segmentos[tipo], _ = Segmento.objects.get_or_create(  # já criados por modulo.0003
            slug=tipo, defaults={"nome": nome, "tipo_precificacao": tipo},
        )

    for cliente in Cliente.objects.all():
        profile = Profile.objects.filter(cliente=cliente).order_by("id").first()
        tipo = profile.segmento_trabalho if profile else "produto"
        cliente.segmento = segmentos.get(tipo, segmentos["produto"])
        cliente.sistema = cliente.nome_razao_social.strip().upper() == NOME_SISTEMA
        cliente.save(update_fields=["segmento", "sistema"])


class Migration(migrations.Migration):
    dependencies = [
        ("cliente", "0005_matriz_filial_segmento"),
        ("accounts", "0004_profile_cliente"),
        ("modulo", "0003_catalogo_de_modulos"),
    ]
    operations = [migrations.RunPython(migrar, migrations.RunPython.noop)]
