"""Conversão entre unidades de medida e preço por unidade.

Modelo: cada medida (tabela `medidas`, Fase 3) tem uma `dimensao` (massa,
volume, comprimento...) e um `fator_base` = quantas "unidades base" da
dimensão ela vale. Convertendo via unidade base, uma única multiplicação/
divisão resolve QUALQUER par de unidades da mesma dimensão, sem tabelas de
conversão hardcoded espalhadas:

    massa        base = grama          (quilo = 1000)
    volume       base = mililitro      (litro = 1000, colher de sopa = 15...)
    comprimento  base = metro          (centímetro = 0,01, quilômetro = 1000)
    area         base = metro quadrado
    tempo        base = segundo
    unidade      base = unidade

As funções recebem qualquer objeto com os atributos `dimensao` e
`fator_base` (normalmente uma `Medida`) — por isso são testáveis sem banco.
"""
from decimal import ROUND_HALF_UP, Decimal

from . import RegraNegocioError
from .dinheiro import para_decimal_seguro

DIMENSAO_MASSA = "massa"
DIMENSAO_VOLUME = "volume"
DIMENSAO_COMPRIMENTO = "comprimento"
DIMENSAO_AREA = "area"
DIMENSAO_TEMPO = "tempo"
DIMENSAO_UNIDADE = "unidade"

DIMENSOES = {
    DIMENSAO_MASSA: "Massa",
    DIMENSAO_VOLUME: "Volume",
    DIMENSAO_COMPRIMENTO: "Comprimento",
    DIMENSAO_AREA: "Área",
    DIMENSAO_TEMPO: "Tempo",
    DIMENSAO_UNIDADE: "Unidade",
}

# Unidade em que o PREÇO é apresentado/guardado para cada dimensão:
# (fator em unidades base, sigla de exibição). Ex.: preço por KG para
# massa (1000 g), por L para volume (1000 mL), por UN para unidade.
UNIDADE_PADRAO_PRECO = {
    DIMENSAO_MASSA: (Decimal("1000"), "KG"),
    DIMENSAO_VOLUME: (Decimal("1000"), "L"),
    DIMENSAO_COMPRIMENTO: (Decimal("1"), "M"),
    DIMENSAO_AREA: (Decimal("1"), "M²"),
    DIMENSAO_TEMPO: (Decimal("1"), "S"),
    DIMENSAO_UNIDADE: (Decimal("1"), "UN"),
}


def sigla_preco_padrao(dimensao):
    """Sigla usada no rótulo 'PREÇO (por KG)' para a dimensão informada."""
    return UNIDADE_PADRAO_PRECO[dimensao][1]


def _fator(medida, papel):
    """Valida e devolve o fator_base de uma medida (> 0)."""
    if medida is None:
        raise RegraNegocioError(f"Unidade de {papel} não informada.")
    try:
        fator = para_decimal_seguro(medida.fator_base, "fator de conversão")
    except RegraNegocioError:
        raise RegraNegocioError(f"Unidade de {papel} sem fator de conversão válido.") from None
    if fator <= 0:
        raise RegraNegocioError(f"Unidade de {papel} com fator de conversão inválido.")
    return fator


def validar_quantidade(quantidade):
    """Quantidade precisa ser número finito e não negativo."""
    valor = para_decimal_seguro(quantidade, "quantidade")
    if valor < 0:
        raise RegraNegocioError("A quantidade não pode ser negativa.")
    return valor


def quantidade_em_base(quantidade, medida):
    """Quantidade expressa na unidade base da dimensão (ex.: 2 kg -> 2000 g)."""
    return validar_quantidade(quantidade) * _fator(medida, "origem")


def converter(quantidade, origem, destino, casas=None):
    """Converte `quantidade` de `origem` para `destino`.

    converter(1000, gramas, quilo)    -> Decimal('1')
    converter(500, mililitro, litro)  -> Decimal('0.5')

    Só converte unidades da MESMA dimensão (não existe "kg -> litro" sem
    densidade). `casas` opcional arredonda o resultado.
    """
    if getattr(origem, "dimensao", None) != getattr(destino, "dimensao", object()):
        raise RegraNegocioError("Não é possível converter entre unidades de dimensões diferentes.")
    resultado = quantidade_em_base(quantidade, origem) / _fator(destino, "destino")
    if casas is not None:
        resultado = resultado.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    return resultado


def preco_por_unidade_padrao(preco_centavos, quantidade, medida):
    """Quanto custa 1 KG / 1 L / 1 UN do insumo, em centavos (int).

    Exemplo: 100 g por R$ 4,00 (400 centavos):
        R$ 4,00 / 100 g = R$ 0,04 por g;  x 1000 = R$ 40,00 por kg -> 4000.

    Cálculo em Decimal; só o resultado final é arredondado para centavos
    (ROUND_HALF_UP) — arredondar etapas intermediárias acumularia erro.
    """
    preco = para_decimal_seguro(preco_centavos, "preço")
    if preco < 0:
        raise RegraNegocioError("O preço não pode ser negativo.")
    quantidade_base = quantidade_em_base(quantidade, medida)
    if quantidade_base == 0:
        raise RegraNegocioError("A quantidade deve ser maior que zero para calcular o preço por unidade.")
    fator_padrao, _sigla = UNIDADE_PADRAO_PRECO[medida.dimensao]
    por_base = preco / quantidade_base
    return int((por_base * fator_padrao).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Metro unidimensional, bidimensional e tridimensional
# --------------------------------------------------------------------------
# Só a unidade "Metro" (comprimento, fator 1) aceita dimensões. Com 1 dimensão a quantidade é o próprio
# comprimento (comportamento de sempre); com 2 ou 3 ela passa a ser o PRODUTO das medidas em metros
# (m², m³) e é gravada em `quantidade`, de modo que custo, preço por unidade e estoque continuam usando a
# mesma coluna e as mesmas contas. Insumo e item só se comparam com o mesmo número de dimensões.
DIMENSOES_METRO = {1: "Linear (comprimento)", 2: "Área (comprimento × largura)", 3: "Volume (comprimento × largura × altura)"}
CASAS_QUANTIDADE = Decimal("0.0001")  # mesma precisão da coluna `quantidade` (4 casas)
_ROTULOS_MEDIDA = ("o comprimento", "a largura", "a altura")


def medida_e_metro(medida):
    """True se a unidade é o metro (comprimento com fator 1) — a única que aceita 2 e 3 dimensões."""
    if getattr(medida, "dimensao", None) != DIMENSAO_COMPRIMENTO:
        return False
    try:
        return para_decimal_seguro(medida.fator_base, "fator de conversão") == 1
    except RegraNegocioError:
        return False


def quantidade_por_dimensoes(dimensoes, comprimento, largura=None, altura=None):
    """Comprimento × largura × altura em metros (só as medidas que a dimensionalidade usa), com 4 casas
    (ROUND_HALF_UP, arredondando só no final). Medida ausente, zero ou negativa é erro; produto que
    arredonda para zero também (ex.: 0,001 × 0,001)."""
    if dimensoes not in DIMENSOES_METRO:
        raise RegraNegocioError("Dimensões inválidas: use 1, 2 ou 3.")
    produto = Decimal(1)
    for rotulo, valor in zip(_ROTULOS_MEDIDA[:dimensoes], (comprimento, largura, altura)):
        if valor is None or valor == "":
            raise RegraNegocioError(f"Informe {rotulo}.")
        numero = para_decimal_seguro(valor, rotulo)
        if numero <= 0:
            raise RegraNegocioError(f"{rotulo.capitalize()} deve ser maior que zero.")
        produto *= numero
    produto = produto.quantize(CASAS_QUANTIDADE, rounding=ROUND_HALF_UP)
    if produto < CASAS_QUANTIDADE:
        raise RegraNegocioError("As dimensões resultam em uma quantidade pequena demais (mínimo 0,0001).")
    return produto


def sigla_preco_dimensoes(medida, dimensoes):
    """Sigla do rótulo de preço: M, M² ou M³ para o metro; a padrão da dimensão para as demais unidades."""
    if medida_e_metro(medida) and dimensoes in (2, 3):
        return "M²" if dimensoes == 2 else "M³"
    return sigla_preco_padrao(medida.dimensao)
