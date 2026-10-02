"""Indicadores de gestão e precificação.

Todas as funções recebem e devolvem Decimal (valores em REAIS; percentuais
em pontos percentuais, ex.: 30 = 30%), validam a entrada e levantam
RegraNegocioError em vez de dividir por zero. Resultados NÃO são
arredondados aqui (o arredondamento comercial para centavos é feito por
DinheiroService na hora de gravar/exibir) para não acumular erro em
cálculos encadeados.
"""
from decimal import Decimal

from . import RegraNegocioError
from .dinheiro import para_decimal_seguro

CEM = Decimal("100")


def _num(valor, campo):
    return para_decimal_seguro(valor, campo)


def _nao_negativo(valor, campo):
    numero = _num(valor, campo)
    if numero < 0:
        raise RegraNegocioError(f"{campo} não pode ser negativo(a).")
    return numero


def _positivo(valor, campo):
    numero = _num(valor, campo)
    if numero <= 0:
        raise RegraNegocioError(f"{campo} deve ser maior que zero.")
    return numero


def custo_unitario(custo_total, quantidade):
    """Custo de 1 unidade: custo_total / quantidade. Serve também para custo
    por KG, por litro ou por unidade — basta informar a quantidade já na
    unidade desejada (use conversao.converter para isso)."""
    return _nao_negativo(custo_total, "O custo total") / _positivo(quantidade, "A quantidade")


def ticket_medio(faturamento, numero_vendas):
    """Ticket médio = faturamento / número de vendas."""
    return _nao_negativo(faturamento, "O faturamento") / _positivo(numero_vendas, "O número de vendas")


def lucro_produto(preco_venda, custo):
    """Lucro (R$) = preço de venda - custo."""
    return _nao_negativo(preco_venda, "O preço de venda") - _nao_negativo(custo, "O custo")


def markup(custo, preco_venda):
    """Markup (%) = (preço - custo) / custo x 100. É o quanto o preço
    ultrapassa o custo, tendo o CUSTO como base."""
    custo_dec = _positivo(custo, "O custo")
    return (_nao_negativo(preco_venda, "O preço de venda") - custo_dec) / custo_dec * CEM


def preco_por_markup(custo, markup_percentual):
    """Preço = custo x (1 + markup/100)."""
    percentual = _nao_negativo(markup_percentual, "O markup")
    return _nao_negativo(custo, "O custo") * (1 + percentual / CEM)


def margem_lucro(preco_venda, custo):
    """Margem (%) = (preço - custo) / preço x 100. Diferente do markup, tem
    o PREÇO como base (por isso nunca passa de 100%)."""
    preco = _positivo(preco_venda, "O preço de venda")
    return (preco - _nao_negativo(custo, "O custo")) / preco * CEM


def preco_por_margem(custo, margem_percentual):
    """Preço = custo / (1 - margem/100). Exige margem < 100%: com 100% o
    preço seria infinito (custo zero)."""
    margem = _nao_negativo(margem_percentual, "A margem")
    if margem >= CEM:
        raise RegraNegocioError("A margem de lucro deve ser menor que 100%.")
    return _nao_negativo(custo, "O custo") / (1 - margem / CEM)


def cmv(estoque_inicial, compras, estoque_final):
    """CMV (Custo da Mercadoria Vendida) = estoque inicial + compras - estoque final."""
    resultado = (
        _nao_negativo(estoque_inicial, "O estoque inicial")
        + _nao_negativo(compras, "As compras")
        - _nao_negativo(estoque_final, "O estoque final")
    )
    if resultado < 0:
        raise RegraNegocioError("O estoque final não pode ser maior que estoque inicial + compras.")
    return resultado


def ponto_equilibrio_unidades(custos_fixos, preco_venda, custo_variavel_unitario):
    """Ponto de equilíbrio (em unidades) = custos fixos / margem de
    contribuição unitária, onde margem de contribuição = preço - custo
    variável. Se a margem de contribuição for <= 0, nunca se atinge o
    equilíbrio."""
    margem_contribuicao = _nao_negativo(preco_venda, "O preço de venda") - _nao_negativo(
        custo_variavel_unitario, "O custo variável unitário"
    )
    if margem_contribuicao <= 0:
        raise RegraNegocioError("O preço de venda deve ser maior que o custo variável para haver ponto de equilíbrio.")
    return _nao_negativo(custos_fixos, "Os custos fixos") / margem_contribuicao


def ponto_equilibrio_valor(custos_fixos, preco_venda, custo_variavel_unitario):
    """Ponto de equilíbrio em R$ = unidades de equilíbrio x preço de venda."""
    unidades = ponto_equilibrio_unidades(custos_fixos, preco_venda, custo_variavel_unitario)
    return unidades * _num(preco_venda, "O preço de venda")


def fator_coccao(peso_antes, peso_depois):
    """Fator de cocção (rendimento) = peso depois / peso antes. Ex.: 1000 g
    de carne crua que viram 700 g -> 0,7."""
    return _nao_negativo(peso_depois, "O peso depois") / _positivo(peso_antes, "O peso antes")


def fator_reducao(peso_antes, peso_depois):
    """Fator de redução (%) = (1 - fator de cocção) x 100: quanto do peso se
    perdeu no preparo. 1000 g -> 700 g = 30%."""
    return (1 - fator_coccao(peso_antes, peso_depois)) * CEM
