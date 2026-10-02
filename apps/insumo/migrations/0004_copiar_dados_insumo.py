"""Passo 2/3: copia os dados do formato antigo para o novo.

  unidade_medida ('KG','G','L','ML','UN')  ->  medida (FK para `medidas`)
  preco (Decimal em reais, 4 casas)        ->  preco_pago (centavos, inteiro)
  (calculado)                              ->  preco_unidade (centavos por KG/L/UN)

Regras de segurança:
- o preço é arredondado para o centavo com ROUND_HALF_UP; se algum registro
  tinha fração de centavo (o campo antigo aceitava 4 casas), isso é
  CONTADO e avisado no console — nenhum valor é descartado em silêncio;
- ao final, a migration CONFERE todos os registros e aborta (revertendo
  tudo) se algum não bater, ANTES de o passo 3 apagar as colunas antigas;
- é reversível: o reverso reconstrói unidade_medida e preco.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

from web_project.regras.conversao import preco_por_unidade_padrao

SIGLA_POR_UNIDADE = {"KG": "kg", "G": "g", "L": "L", "ML": "ml", "UN": "un"}
UNIDADE_POR_SIGLA = {sigla: unidade for unidade, sigla in SIGLA_POR_UNIDADE.items()}
CEM = Decimal("100")


def copiar_para_formato_novo(apps, schema_editor):
    Insumo = apps.get_model("insumo", "Insumo")
    Medida = apps.get_model("medida", "Medida")
    medidas = {unidade: Medida.objects.get(sigla=sigla) for unidade, sigla in SIGLA_POR_UNIDADE.items()}

    com_fracao_de_centavo = 0
    for insumo in Insumo.objects.all():
        medida = medidas[insumo.unidade_medida]
        bruto = insumo.preco * CEM
        centavos = int(bruto.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if bruto != bruto.to_integral_value():
            com_fracao_de_centavo += 1
        preco_unidade = (
            preco_por_unidade_padrao(centavos, insumo.quantidade, medida) if insumo.quantidade > 0 else None
        )
        Insumo.objects.filter(pk=insumo.pk).update(medida=medida, preco_pago=centavos, preco_unidade=preco_unidade)

    # Validação ANTES de o passo 3 remover as colunas antigas.
    for insumo in Insumo.objects.all():
        esperado = int((insumo.preco * CEM).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if insumo.medida_id is None or insumo.preco_pago != esperado:
            raise RuntimeError(f"Conversão inconsistente no insumo id={insumo.pk}; migração abortada.")

    if com_fracao_de_centavo:
        print(f"\n  AVISO: {com_fracao_de_centavo} insumo(s) tinham fração de centavo no preço e foram arredondados.")


def restaurar_formato_antigo(apps, schema_editor):
    Insumo = apps.get_model("insumo", "Insumo")
    for insumo in Insumo.objects.select_related("medida"):
        unidade = UNIDADE_POR_SIGLA.get(insumo.medida.sigla, "UN") if insumo.medida_id else "UN"
        Insumo.objects.filter(pk=insumo.pk).update(
            unidade_medida=unidade, preco=Decimal(insumo.preco_pago) / CEM
        )


class Migration(migrations.Migration):
    dependencies = [("insumo", "0003_insumo_medida_preco_centavos")]
    operations = [migrations.RunPython(copiar_para_formato_novo, restaurar_formato_antigo)]
