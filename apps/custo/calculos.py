"""Regras de cálculo do custo operacional por categoria (funções puras).

    custo operacional total   = soma dos custos ATIVOS da unidade (valores mensais, em centavos)
    custo da categoria        = total × (vendas da categoria ÷ vendas de todas as categorias)
    custo operacional/unidade = custo da categoria ÷ quantidade vendida da categoria

Exemplo: água + luz + gás = R$ 300 e três categorias que vendem 100 itens cada ->
R$ 100 por categoria (R$ 1 por item). Se vendessem 200, 100 e 0, receberiam R$ 200, R$ 100 e R$ 0.
Veja `distribuir_por_vendas` (arredondamento exato em centavos, ajustes manuais, sem vendas).

A quantidade vendida da categoria (apps/categoria) deve ser a do MESMO período
dos custos (mensal), senão o custo por unidade perde o sentido. Sem vendas
informadas (quantidade 0) o custo por unidade é 0 — nunca divisão por zero — e a
tela avisa. O valor por unidade é só uma SUGESTÃO para a Precificação: ela copia
o valor ao usuário, que pode ajustá-lo (nada é recalculado sozinho depois).

Valores em centavos (int); percentuais em pontos percentuais (Decimal).
Arredonda uma única vez (ROUND_HALF_UP), no resultado final de cada total.
"""
from decimal import ROUND_HALF_UP, Decimal

from web_project.regras import RegraNegocioError

CEM = Decimal("100")


def parcela(valor_centavos, percentual):
    """Parte do custo (em centavos, ainda sem arredondar) que cabe ao percentual."""
    return Decimal(valor_centavos) * Decimal(percentual) / CEM


def em_centavos(valor):
    """Arredonda um Decimal em centavos para inteiro (ROUND_HALF_UP)."""
    return int(Decimal(valor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


ORIGEM_MANUAL, ORIGEM_VENDAS, ORIGEM_IGUAL, ORIGEM_ZERO = "manual", "vendas", "igual", "zero"


def _repartir(total, pesos):
    """Reparte `total` (centavos, int) proporcionalmente a `pesos` ({id: peso inteiro > 0})
    pelo método do MAIOR RESTO: cada um recebe o piso da sua fatia e os centavos que sobram
    vão, um a um, para quem teve o maior resto (empate: maior peso, depois menor id). A soma
    é sempre exatamente `total`."""
    soma = sum(pesos.values())
    partes = {i: (total * p) // soma for i, p in pesos.items()}
    sobra = total - sum(partes.values())
    ordem = sorted(pesos, key=lambda i: (-((total * pesos[i]) % soma), -pesos[i], i))
    for i in ordem[:sobra]:
        partes[i] += 1
    return partes


def distribuir_por_vendas(total, categorias, ajustes=None):
    """Distribui o custo operacional `total` (centavos) entre as categorias, em reais.

    `categorias`: lista de (id, quantidade_vendida, elegivel) — elegível = pode receber
    rateio automático (categoria ativa). `ajustes`: {id: centavos} decididos pelo usuário.

    Regras:
    1. Categoria com ajuste manual recebe exatamente o valor ajustado.
    2. O restante (total − ajustes) vai para as categorias elegíveis SEM ajuste, na proporção
       das vendas; categoria sem vendas recebe 0.
    3. Se NENHUMA categoria elegível tem vendas (sem histórico), o restante é dividido
       igualmente entre as elegíveis (origem "igual") — a tela avisa.
    4. Se não há categoria para receber o restante, ele aparece como `nao_distribuido`.
    5. Se os ajustes passam do total, as demais recebem 0 e o excesso aparece em `excedente_manual`.

    Devolve ({id: {"valor", "origem"}}, nao_distribuido, excedente_manual); a soma de
    valores + nao_distribuido é exatamente `total` (exceto no caso 5)."""
    ajustes = {i: int(v) for i, v in (ajustes or {}).items() if i in {c[0] for c in categorias}}
    resultado = {i: {"valor": v, "origem": ORIGEM_MANUAL} for i, v in ajustes.items()}
    restante = int(total) - sum(ajustes.values())
    automaticas = [(i, int(qtd)) for i, qtd, elegivel in categorias if elegivel and i not in ajustes]
    for i, _ in automaticas:
        resultado[i] = {"valor": 0, "origem": ORIGEM_ZERO}
    if restante < 0:
        return resultado, 0, -restante
    if not automaticas:
        return resultado, restante, 0
    com_vendas = {i: q for i, q in automaticas if q > 0}
    if com_vendas:
        origem, pesos = ORIGEM_VENDAS, com_vendas
    else:
        origem, pesos = ORIGEM_IGUAL, {i: 1 for i, _ in automaticas}
    if restante:
        for i, valor in _repartir(restante, pesos).items():
            resultado[i] = {"valor": valor, "origem": origem}
    return resultado, 0, 0


def custo_por_unidade(total_centavos, quantidade_vendida):
    """Custo da categoria dividido pela quantidade vendida, em centavos; 0 sem vendas."""
    if not quantidade_vendida:
        return 0
    return em_centavos(Decimal(total_centavos) / Decimal(quantidade_vendida))


def validar_distribuicao(percentuais):
    """A distribuição de UM custo: cada parcela entre 0 e 100 (exclusive 0) e a
    soma exatamente 100 %. Lista vazia é válida (custo "não distribuído").
    Levanta RegraNegocioError com a soma atual para o usuário corrigir."""
    percentuais = [Decimal(p) for p in percentuais]
    if not percentuais:
        return
    if any(p <= 0 or p > CEM for p in percentuais):
        raise RegraNegocioError("Cada percentual deve ser maior que 0 e no máximo 100.")
    soma = sum(percentuais)
    if soma != CEM:
        raise RegraNegocioError(f"A distribuição deve somar 100% (soma atual: {soma.normalize():f}%).".replace(".", ","))
