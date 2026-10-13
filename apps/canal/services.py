"""Gravação de canais de venda: canal + taxas + totais + auditoria, numa transação."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto

from .models import CanalTaxa, CanalVenda

LIMITE_PERCENTUAL = Decimal("100")


def calcular_totais(taxas):
    """(percentual total, fixo total em centavos) de uma lista de CanalTaxa.
    Percentuais e fixos são somados separadamente: nunca se misturam."""
    percentual = sum((t.percentual for t in taxas if t.percentual is not None), Decimal("0"))
    fixo = sum(t.valor_fixo for t in taxas if t.valor_fixo is not None)
    return percentual, fixo


def validar_taxas(taxas):
    """O total percentual precisa ficar abaixo de 100%: com 100% ou mais o
    canal levaria todo o preço e a precificação (preço = custo ÷ líquido) não
    teria resultado."""
    percentual, _fixo = calcular_totais(taxas)
    if percentual >= LIMITE_PERCENTUAL:
        raise ValidationError("A soma das taxas percentuais deve ser menor que 100%.")


def _texto_taxas(canal):
    return " + ".join(t.texto for t in canal.taxas.all()) or None


def salvar_canal(request, canal, taxas, foto_antes=None):
    """Grava o canal e SUBSTITUI as taxas por `taxas` (lista de CanalTaxa sem
    canal), recalculando os totais. Tudo ou nada."""
    validar_taxas(taxas)
    criando = canal.pk is None
    with transaction.atomic():
        antes_taxas = None if criando else _texto_taxas(canal)
        percentual, fixo = calcular_totais(taxas)
        canal.taxa_percentual_total, canal.taxa_fixa_total = percentual, fixo
        canal.save()
        canal.taxas.all().delete()
        for taxa in taxas:
            taxa.canal = canal
        CanalTaxa.objects.bulk_create(taxas)
        if not criando:  # precificações que usam o canal passam a usar as taxas novas
            from apps.precificacao.services import recalcular_do_canal  # import tardio: evita ciclo entre apps

            recalcular_do_canal(canal)

        if criando:
            registrar_criacao(request, canal)
        else:
            alteracoes = comparar(foto_antes, tirar_foto(canal)) if foto_antes else []
            depois_taxas = _texto_taxas(canal)
            if antes_taxas != depois_taxas:
                alteracoes.append({"campo": "taxas", "rotulo": "Taxas", "antes": antes_taxas, "depois": depois_taxas})
            if alteracoes:
                registrar_alteracoes(request, canal, alteracoes)
    return canal


__all__ = ["CanalVenda", "salvar_canal", "calcular_totais", "validar_taxas"]
