"""Indicadores de venda por categoria. Valores neutros (zero) quando não há
vendas/faturamento, nunca divisão por zero."""
from decimal import ROUND_HALF_UP, Decimal

from web_project.regras.indicadores import ticket_medio


def ticket_medio_centavos(faturamento, quantidade):
    """Faturamento ÷ quantidade vendida, em centavos (0 sem vendas)."""
    if not quantidade:
        return 0
    return int(ticket_medio(faturamento, quantidade).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def percentual_do_total(faturamento, total):
    """Participação da categoria no faturamento total, em pontos percentuais
    com 2 casas (0,00 quando o total é zero)."""
    if not total:
        return Decimal("0.00")
    return (Decimal(faturamento) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
