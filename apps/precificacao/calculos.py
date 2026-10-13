"""Fórmulas da precificação (funções puras: sem banco, só Decimal e inteiros).

Valores em CENTAVOS (int); percentuais em pontos percentuais (10 = 10 %).

Ordem das operações
-------------------
1. Custo total (antes do canal e do lucro)   C = custo da ficha + custo operacional
2. Canal                                     t = taxas percentuais ÷ 100 (incidem sobre o preço de venda)
                                             F = taxas fixas em reais (por venda)
3. Preço de venda P, conforme o método do lucro — a ordem garante que as taxas
   do canal e o lucro saiam do MESMO preço, sem circularidade:

   • Valor em reais (L):    P = (C + L + F) ÷ (1 − t)
   • Markup k % sobre o CUSTO:  P = (C × (1 + k/100) + F) ÷ (1 − t)
   • Margem m % sobre o PREÇO DE VENDA:  P = (C + F) ÷ (1 − t − m/100)

   Markup e margem são fórmulas diferentes de propósito: o markup tem o custo
   como base (pode passar de 100 %); a margem tem o preço como base (nunca chega
   a 100 %), e com ela o lucro é exatamente m % de P depois de pagar custo e canal.
4. P é arredondado PARA CIMA ao centavo (ROUND_CEILING): arredondar para baixo
   comeria a margem pedida.
5. Taxas percentuais em reais = P × t (ROUND_HALF_UP); lucro = P − C − F − taxas
   percentuais. Assim custo + taxas + lucro somam exatamente P, sem sobra.
6. CMV: custo da mercadoria vendida por unidade = custo da FICHA (os insumos; o
   custo operacional não é mercadoria). CMV % = CMV ÷ P × 100.
   (É o mesmo conceito de `indicadores.cmv`, estoque inicial + compras − estoque
   final, aplicado a uma unidade vendida.)

Casos de borda: sem taxas (t = 0, F = 0) a conta segue normal; P = 0 (custo e
lucro zerados) dá CMV 0 % (sem divisão por zero); margem + taxas ≥ 100 % é erro
claro, pois o preço seria infinito ou negativo.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from web_project.regras import RegraNegocioError

METODO_MARGEM = "margem"
METODO_MARKUP = "markup"
METODO_VALOR = "valor"

CEM = Decimal("100")
LIMITE_CENTAVOS = 10**15  # teto de segurança, como nos insumos


@dataclass(frozen=True)
class ResultadoPreco:
    custo_ficha: int
    custo_operacional: int
    custo_total: int
    taxa_percentual: Decimal
    taxa_fixa: int
    taxas_percentuais_valor: int
    lucro: int
    preco_venda: int
    cmv_percentual: Decimal


def _inteiro_nao_negativo(valor, campo):
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise RegraNegocioError(f"{campo} deve ser um valor em centavos não negativo.")
    if valor > LIMITE_CENTAVOS:
        raise RegraNegocioError(f"{campo} acima do limite permitido.")
    return valor


def calcular_preco(custo_ficha, custo_operacional, taxa_percentual, taxa_fixa, metodo, percentual_lucro=Decimal("0"), valor_lucro=0):
    """Calcula o preço de venda e a decomposição (ver o módulo). Levanta
    RegraNegocioError para entradas inválidas ou fórmulas sem resultado."""
    custo_ficha = _inteiro_nao_negativo(custo_ficha, "O custo da ficha")
    custo_operacional = _inteiro_nao_negativo(custo_operacional, "O custo operacional")
    taxa_fixa = _inteiro_nao_negativo(taxa_fixa, "A taxa fixa")
    valor_lucro = _inteiro_nao_negativo(valor_lucro, "O lucro em reais")
    taxa_percentual = Decimal(taxa_percentual)
    percentual_lucro = Decimal(percentual_lucro)
    if not (Decimal("0") <= taxa_percentual < CEM):
        raise RegraNegocioError("As taxas percentuais devem estar entre 0% e 100%.")
    if percentual_lucro < 0:
        raise RegraNegocioError("A margem ou o markup não pode ser negativo.")

    custo = Decimal(custo_ficha + custo_operacional)
    fixas = Decimal(taxa_fixa)
    t = taxa_percentual / CEM

    if metodo == METODO_VALOR:
        preco = (custo + valor_lucro + fixas) / (1 - t)
    elif metodo == METODO_MARKUP:
        preco = (custo * (1 + percentual_lucro / CEM) + fixas) / (1 - t)
    elif metodo == METODO_MARGEM:
        divisor = 1 - t - percentual_lucro / CEM
        if divisor <= 0:
            raise RegraNegocioError("A soma da margem e das taxas percentuais do canal deve ser menor que 100%.")
        preco = (custo + fixas) / divisor
    else:
        raise RegraNegocioError("Método de formação do lucro inválido.")

    preco_venda = int(preco.quantize(Decimal(1), rounding=ROUND_CEILING))
    if preco_venda > LIMITE_CENTAVOS:
        raise RegraNegocioError("O preço de venda calculado é grande demais; confira os valores informados.")
    taxas_percentuais = int((Decimal(preco_venda) * t).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    lucro = preco_venda - int(custo) - taxa_fixa - taxas_percentuais
    cmv = (Decimal(custo_ficha) * CEM / Decimal(preco_venda)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if preco_venda else Decimal("0.00")
    return ResultadoPreco(
        custo_ficha=custo_ficha, custo_operacional=custo_operacional, custo_total=int(custo), taxa_percentual=taxa_percentual,
        taxa_fixa=taxa_fixa, taxas_percentuais_valor=taxas_percentuais, lucro=lucro, preco_venda=preco_venda, cmv_percentual=cmv,
    )
