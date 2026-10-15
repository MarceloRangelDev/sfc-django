"""Filtro `moeda`: centavos (inteiro) -> "R$ 1.234,56". Disponível em todos os templates (`builtins` em config/settings.py).
O dinheiro do sistema é sempre inteiro em centavos; este filtro só formata (a regra está em web_project/regras/dinheiro.py)."""
from django import template

from web_project.regras.dinheiro import DinheiroService

register = template.Library()


@register.filter
def moeda(centavos):
    if centavos in (None, ""):
        return "—"
    try:
        return DinheiroService.formatar(int(centavos))
    except (TypeError, ValueError):
        return "—"
