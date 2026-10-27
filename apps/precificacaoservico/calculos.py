"""Cálculos da precificação de serviços: funções PURAS (sem banco), com Decimal e centavos inteiros.

Tela, API e simulador usam exatamente estas funções; nada é calculado no navegador. Ver README do app para as fórmulas.

Conceitos (não se misturam):
- custo: o que o serviço consome (trabalho = horas x custo-hora, que já inclui a REMUNERAÇÃO do profissional, + custos diretos);
- impostos/taxas: percentuais sobre o PREÇO de venda (estimativas informadas pelo usuário, não orientação tributária);
- margem: lucro como % do PREÇO. Markup: acréscimo como % do CUSTO. São fórmulas distintas;
- lucro: o que sobra do preço depois de custo (inclusive remuneração), impostos e taxas.
"""
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from web_project.regras import RegraNegocioError

CEM = Decimal("100")
LIMITE_CENTAVOS = 10**12  # R$ 10 bilhões: protege contra estouro de coluna e entradas absurdas
HORAS_MAX_MES = Decimal("744")

# Periodicidade de uma despesa -> (multiplicador, divisor) para chegar ao valor MENSAL (semanal = 52 semanas / 12 meses).
# Fração em vez de Decimal(1)/3 evita erro de arredondamento em 1/3 e 1/6 (30000 trimestral = exatamente 10000 por mês).
FATOR_MENSAL = {"semanal": (52, 12), "mensal": (1, 1), "bimestral": (1, 2), "trimestral": (1, 3), "semestral": (1, 6), "anual": (1, 12)}

# Formas de cobrança e dias de trabalho que cada período contém (None = depende do cadastro do item).
DIAS_DO_PERIODO = {"dia": 1, "semana": 5, "quinzena": 10}
MODOS = ("hora", "dia", "semana", "quinzena", "mes", "unidade", "pacote")
REPASSES = ("discriminado", "incluido", "absorvido")


def dec(valor, campo="valor"):
    """Converte para Decimal recusando float, bool e valores não finitos."""
    if isinstance(valor, float) or isinstance(valor, bool) or valor is None:
        raise RegraNegocioError(f"{campo}: informe um número válido.")
    try:
        numero = valor if isinstance(valor, Decimal) else Decimal(str(valor))
    except Exception:
        raise RegraNegocioError(f"{campo}: informe um número válido.") from None
    if not numero.is_finite():
        raise RegraNegocioError(f"{campo}: informe um número válido.")
    return numero


def centavos(valor, arredondamento=ROUND_HALF_UP):
    """Decimal (centavos, possivelmente fracionário) -> int de centavos."""
    return int(valor.quantize(Decimal(1), rounding=arredondamento))


def _centavos_validos(valor, campo, minimo=0):
    numero = dec(valor, campo)
    if numero != numero.to_integral_value():
        raise RegraNegocioError(f"{campo}: informe o valor em centavos inteiros.")
    if numero < minimo or numero > LIMITE_CENTAVOS:
        raise RegraNegocioError(f"{campo}: valor fora do limite permitido.")
    return int(numero)


def _percentual(valor, campo, maximo=Decimal("99.99")):
    numero = dec(valor, campo)
    if numero < 0 or numero > maximo:
        raise RegraNegocioError(f"{campo}: informe um percentual entre 0 e {maximo}.")
    return numero


def valor_mensal(valor, periodicidade):
    """Despesa (centavos) na periodicidade informada -> Decimal de centavos por MÊS."""
    if periodicidade not in FATOR_MENSAL:
        raise RegraNegocioError("Periodicidade inválida.")
    multiplicador, divisor = FATOR_MENSAL[periodicidade]
    return Decimal(_centavos_validos(valor, "Valor da despesa") * multiplicador) / divisor


# ---------------------------------------------------------------------------------------------------------------------------
# Capacidade e custo-hora
# ---------------------------------------------------------------------------------------------------------------------------

def capacidade(dias_mes, horas_dia, horas_nao_faturaveis, horas_faturaveis_informadas=None):
    """Horas disponíveis x faturáveis do mês.

    disponíveis = dias de trabalho no mês x horas por dia; faturáveis = disponíveis - não faturáveis (administrativo, orçamento,
    cobrança, deslocamento não cobrado), a menos que o usuário informe as faturáveis diretamente. Faturáveis <= 0 bloqueia o cálculo."""
    dias, horas = dec(dias_mes, "Dias de trabalho"), dec(horas_dia, "Horas por dia")
    if not 0 < dias <= 31:
        raise RegraNegocioError("Dias de trabalho no mês: informe um valor entre 0 e 31.")
    if not 0 < horas <= 24:
        raise RegraNegocioError("Horas de trabalho por dia: informe um valor entre 0 e 24.")
    disponiveis = dias * horas
    nao_faturaveis = dec(horas_nao_faturaveis, "Horas não faturáveis")
    if nao_faturaveis < 0:
        raise RegraNegocioError("Horas não faturáveis não podem ser negativas.")
    if horas_faturaveis_informadas is not None:
        faturaveis = dec(horas_faturaveis_informadas, "Horas faturáveis")
        if faturaveis > disponiveis:
            raise RegraNegocioError("As horas faturáveis não podem passar das horas disponíveis no mês.")
    else:
        faturaveis = disponiveis - nao_faturaveis
    if faturaveis <= 0 or faturaveis > HORAS_MAX_MES:
        raise RegraNegocioError("As horas faturáveis do mês devem ser maiores que zero. Reduza as horas não faturáveis ou aumente a jornada.")
    return {"horas_disponiveis": disponiveis, "horas_nao_faturaveis": disponiveis - faturaveis, "horas_faturaveis": faturaveis}


def custo_hora(remuneracao, despesas_mensais, provisoes_mensais, horas_faturaveis, variavel_por_hora=0):
    """Custo-hora total, com a composição (cada linha em centavos, Decimal).

    custo fixo mensal = remuneração + despesas fixas mensais + provisões mensais
    custo fixo por hora faturável = custo fixo mensal / horas faturáveis
    custo-hora total = custo fixo por hora + custo variável por hora
    A remuneração fica em linha própria: ela entra no custo-base mas NÃO é lucro."""
    horas = dec(horas_faturaveis, "Horas faturáveis")
    if horas <= 0:
        raise RegraNegocioError("Não é possível calcular o custo-hora com horas faturáveis zeradas ou negativas.")
    remuneracao = Decimal(_centavos_validos(remuneracao, "Remuneração mensal"))
    variavel = Decimal(_centavos_validos(variavel_por_hora, "Custo variável por hora"))
    despesas, provisoes = dec(despesas_mensais), dec(provisoes_mensais)
    fixo_mensal = remuneracao + despesas + provisoes
    fixo_hora = fixo_mensal / horas
    return {
        "remuneracao_mensal": remuneracao, "despesas_mensais": despesas, "provisoes_mensais": provisoes, "custo_fixo_mensal": fixo_mensal,
        "horas_faturaveis": horas, "custo_fixo_hora": fixo_hora, "custo_variavel_hora": variavel, "custo_hora_total": fixo_hora + variavel,
        "remuneracao_hora": remuneracao / horas,
    }


# ---------------------------------------------------------------------------------------------------------------------------
# Preço de venda
# ---------------------------------------------------------------------------------------------------------------------------

def validar_percentuais(impostos, taxas, margem=Decimal(0)):
    """Impostos, taxas e margem são % do PREÇO: a soma tem de ficar abaixo de 100%, senão o preço não existe (divisão por zero/negativo)."""
    impostos, taxas, margem = _percentual(impostos, "Impostos"), _percentual(taxas, "Taxas"), _percentual(margem, "Margem")
    if impostos + taxas + margem >= CEM:
        raise RegraNegocioError(
            f"Impostos ({impostos}%) + taxas ({taxas}%) + margem ({margem}%) somam {impostos + taxas + margem}%. A soma precisa ser menor que 100%, "
            "pois todos incidem sobre o preço de venda; reduza algum deles."
        )
    return impostos / CEM, taxas / CEM, margem / CEM


def preco_por_margem(custo, impostos, taxas, margem):
    """Preço = custo / (1 - impostos - taxas - margem), arredondado PARA CIMA ao centavo (para baixo comeria a margem pedida)."""
    i, t, m = validar_percentuais(impostos, taxas, margem)
    return centavos(Decimal(custo) / (1 - i - t - m), ROUND_CEILING)


def preco_por_markup(custo, markup, impostos, taxas):
    """Markup é % sobre o CUSTO: preço = custo x (1 + markup); impostos/taxas incidem sobre o preço (checados). Não é margem."""
    validar_percentuais(impostos, taxas)
    markup = dec(markup, "Markup")
    if markup < 0 or markup > 10000:
        raise RegraNegocioError("Markup: informe um percentual entre 0 e 10000.")
    return centavos(Decimal(custo) * (1 + markup / CEM), ROUND_CEILING)


# ---------------------------------------------------------------------------------------------------------------------------
# Proposta
# ---------------------------------------------------------------------------------------------------------------------------

def horas_padrao_do_modo(modo, premissas):
    """Horas que UMA unidade de cada forma de cobrança representa, quando o item não informa. Dia = jornada; semana = 5 dias; quinzena = 10 dias;
    mês = horas faturáveis do mês. Unidade e pacote não têm padrão (o esforço é informado, ou 0)."""
    horas_dia = Decimal(premissas["horas_dia"])
    if modo == "hora":
        return Decimal(1)
    if modo in DIAS_DO_PERIODO:
        return horas_dia * DIAS_DO_PERIODO[modo]
    if modo == "mes":
        return Decimal(premissas["horas_faturaveis"])
    return Decimal(0)


def _blocos(horas, minutos):
    """Arredonda horas PARA CIMA em blocos de `minutos` (0 = sem arredondamento)."""
    if not minutos:
        return horas
    bloco = Decimal(minutos) / 60
    return (horas / bloco).to_integral_value(rounding=ROUND_CEILING) * bloco


def _validar_itens(itens):
    saida = []
    for numero, item in enumerate(itens, 1):
        rotulo = f"Item {numero}"
        if item.get("modo") not in MODOS:
            raise RegraNegocioError(f"{rotulo}: forma de cobrança inválida.")
        quantidade = dec(item.get("quantidade"), f"{rotulo} — quantidade")
        if not 0 < quantidade <= Decimal("1000000"):
            raise RegraNegocioError(f"{rotulo}: a quantidade deve ser maior que zero.")
        horas = item.get("horas_por_unidade")
        horas = None if horas in (None, "") else dec(horas, f"{rotulo} — horas por unidade")
        if horas is not None and not 0 <= horas <= 1000:
            raise RegraNegocioError(f"{rotulo}: horas por unidade fora do limite.")
        preco = item.get("preco_unitario")
        preco = None if preco in (None, "") else _centavos_validos(preco, f"{rotulo} — preço unitário")
        saida.append({"modo": item["modo"], "quantidade": quantidade, "horas_por_unidade": horas, "preco_unitario": preco})
    return saida


def _validar_custos(custos):
    saida = []
    for numero, custo in enumerate(custos, 1):
        rotulo = f"Custo {numero}"
        if custo.get("repasse") not in REPASSES:
            raise RegraNegocioError(f"{rotulo}: escolha como o custo entra no orçamento.")
        quantidade = dec(custo.get("quantidade"), f"{rotulo} — quantidade")
        if not 0 < quantidade <= Decimal("1000000"):
            raise RegraNegocioError(f"{rotulo}: a quantidade deve ser maior que zero.")
        unitario = _centavos_validos(custo.get("custo_unitario"), f"{rotulo} — custo unitário")
        saida.append({"repasse": custo["repasse"], "valor": centavos(quantidade * unitario)})
    return saida


def calcular_proposta(entrada, premissas):
    """Calcula custo, preços, impostos, lucro e margem de uma proposta.

    `premissas`: retrato do custo-hora/percentuais (calculos.montar_premissas) — NUNCA a configuração viva, para que propostas antigas
    não mudem. `entrada`: itens, custos, metodo_preco, markup, margem, preco_fechado, desconto/acréscimos, mínimos e deslocamento.
    Devolve um dict só com int (centavos), strings decimais e listas (serializável em JSON)."""
    itens = _validar_itens(entrada.get("itens") or [])
    custos = _validar_custos(entrada.get("custos") or [])
    if not itens and not custos:
        raise RegraNegocioError("Inclua ao menos um item de trabalho ou um custo.")

    custo_hora_total = Decimal(premissas["custo_hora_total"])
    impostos_pct = dec(premissas["impostos_pct"])
    taxas_pct = dec(premissas["taxas_pct"]) + _percentual(entrada.get("custo_financeiro_pct") or 0, "Custo financeiro (parcelamento)")
    margem_pct = _percentual(entrada["margem_pct"] if entrada.get("margem_pct") is not None else premissas["margem_pct"], "Margem")
    metodo = entrada.get("metodo_preco") or "margem"
    if metodo not in ("margem", "markup"):
        raise RegraNegocioError("Método de preço inválido.")
    # Valida SEMPRE, também no markup (impostos + taxas já podem ser inviáveis) e antes de qualquer divisão.
    imp, tx, mg = validar_percentuais(impostos_pct, taxas_pct, margem_pct if metodo == "margem" else 0)
    encargos = imp + tx

    # --- horas ----------------------------------------------------------------------------------------------------------
    arredondamento = int(entrada.get("arredondamento_minutos") or 0)
    if arredondamento not in (0, 5, 10, 15, 30, 60):
        raise RegraNegocioError("Arredondamento de horas: use 0, 5, 10, 15, 30 ou 60 minutos.")
    horas_minimas = dec(entrada.get("horas_minimas") or 0, "Horas mínimas")
    horas_itens, preco_itens, todos_com_preco = [], 0, bool(itens)
    for item in itens:
        por_unidade = item["horas_por_unidade"] if item["horas_por_unidade"] is not None else horas_padrao_do_modo(item["modo"], premissas)
        quantidade = _blocos(item["quantidade"], arredondamento) if item["modo"] == "hora" else item["quantidade"]
        horas_itens.append([item["modo"], quantidade, por_unidade])
    # Cobrança mínima em horas: o que faltar entra no primeiro item cobrado por hora.
    horas_cobradas = sum(q for m, q, _ in horas_itens if m == "hora")
    if horas_minimas and any(m == "hora" for m, _, _ in horas_itens) and horas_cobradas < horas_minimas:
        primeiro = next(linha for linha in horas_itens if linha[0] == "hora")
        primeiro[1] += horas_minimas - horas_cobradas
    horas_trabalho = Decimal(0)
    for item, (modo, quantidade, por_unidade) in zip(itens, horas_itens):
        horas_trabalho += quantidade * por_unidade
        if item["preco_unitario"] is None:
            todos_com_preco = False
        else:
            preco_itens += centavos(quantidade * item["preco_unitario"])
    horas_deslocamento = dec(entrada.get("horas_deslocamento") or 0, "Horas de deslocamento")
    if not 0 <= horas_deslocamento <= 1000:
        raise RegraNegocioError("Horas de deslocamento fora do limite.")
    cobra_deslocamento = bool(entrada.get("cobra_deslocamento"))

    # --- custos ---------------------------------------------------------------------------------------------------------
    custo_trabalho = centavos(horas_trabalho * custo_hora_total)
    custo_deslocamento = centavos(horas_deslocamento * custo_hora_total)
    por_repasse = {r: sum(c["valor"] for c in custos if c["repasse"] == r) for r in REPASSES}
    custo_total = custo_trabalho + custo_deslocamento + sum(por_repasse.values())
    # Base do preço: trabalho + custos "incluídos no preço" (+ deslocamento só se for cobrado). Absorvido pesa no custo, não no preço;
    # discriminado é cobrado à parte, pelo valor do custo (repasse), coberto apenas de impostos/taxas — sem margem.
    base = custo_trabalho + por_repasse["incluido"] + (custo_deslocamento if cobra_deslocamento else 0)
    discriminado = por_repasse["discriminado"]
    repasse_cobrado = centavos(Decimal(discriminado) / (1 - encargos), ROUND_CEILING) if discriminado else 0
    if metodo == "margem":
        preco_alvo = centavos(Decimal(base) / (1 - encargos - mg), ROUND_CEILING) + repasse_cobrado
    else:
        markup = dec(entrada.get("markup_pct") or 0, "Markup")
        if not 0 <= markup <= 10000:
            raise RegraNegocioError("Markup: informe um percentual entre 0 e 10000.")
        preco_alvo = centavos(Decimal(base) * (1 + markup / CEM), ROUND_CEILING) + repasse_cobrado
    # Preço mínimo: cobre TODO o custo (também o absorvido) e os percentuais obrigatórios, com lucro zero.
    preco_minimo = centavos(Decimal(custo_total - discriminado) / (1 - encargos), ROUND_CEILING) + repasse_cobrado

    # --- preço praticado --------------------------------------------------------------------------------------------------
    alertas = []
    fechado = entrada.get("preco_fechado")
    if fechado not in (None, ""):
        venda = _centavos_validos(fechado, "Preço fechado")
        origem = "preco_fechado"
    elif itens and todos_com_preco:
        # Todos os itens de trabalho têm preço unitário: vale quantidade x preço; deslocamento cobrado e repasses entram por cima.
        extra_desloc = centavos(Decimal(custo_deslocamento) / (1 - encargos - (mg if metodo == "margem" else 0)), ROUND_CEILING) if cobra_deslocamento else 0
        venda = preco_itens + extra_desloc + repasse_cobrado
        origem = "precos_dos_itens"
    else:
        venda = preco_alvo
        origem = "preco_alvo"
        if itens and any(i["preco_unitario"] is not None for i in itens):
            alertas.append({"nivel": "aviso", "codigo": "itens_sem_preco", "mensagem": "Há itens sem preço unitário; o preço foi calculado pelo preço-alvo."})
    minimo = _centavos_validos(entrada.get("minimo_cobranca") or 0, "Cobrança mínima")
    if venda < minimo:
        venda, origem = minimo, "cobranca_minima"
    acrescimo_pct = _percentual(entrada.get("acrescimo_pct") or 0, "Acréscimo (urgência, risco, fim de semana)", Decimal("1000"))
    acrescimo_pct_valor = centavos(Decimal(venda) * acrescimo_pct / CEM)
    acrescimo_valor = _centavos_validos(entrada.get("acrescimo_valor") or 0, "Acréscimo em valor")
    bruto = venda + acrescimo_pct_valor + acrescimo_valor

    desconto_tipo = entrada.get("desconto_tipo") or "valor"
    desconto_informado = dec(entrada.get("desconto") or 0, "Desconto")
    if desconto_informado < 0:
        raise RegraNegocioError("O desconto não pode ser negativo.")
    if desconto_tipo == "percentual":
        if desconto_informado > 100:
            raise RegraNegocioError("O desconto percentual não pode passar de 100%.")
        desconto = centavos(Decimal(bruto) * desconto_informado / CEM)
    elif desconto_tipo == "valor":
        desconto = _centavos_validos(desconto_informado, "Desconto")
    else:
        raise RegraNegocioError("Tipo de desconto inválido.")
    if desconto > bruto:
        raise RegraNegocioError("O desconto não pode ser maior que o valor da proposta.")
    total = bruto - desconto

    # --- resultado -------------------------------------------------------------------------------------------------------
    encargos_valor = centavos(Decimal(total) * encargos)
    lucro = total - custo_total - encargos_valor
    margem = (Decimal(lucro) / total * CEM).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total else None
    markup_resultante = (Decimal(total - custo_total) / custo_total * CEM).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if custo_total else None
    margem_desejada = margem_pct if metodo == "margem" else None

    if total < custo_total:
        alertas.append({"nivel": "erro", "codigo": "abaixo_do_custo", "mensagem": "O preço está abaixo do custo estimado: o serviço dá prejuízo."})
    elif lucro < 0:
        alertas.append({"nivel": "erro", "codigo": "prejuizo", "mensagem": "Depois de impostos e taxas estimados, o resultado é negativo."})
    elif total < preco_minimo:
        alertas.append({"nivel": "aviso", "codigo": "abaixo_do_minimo", "mensagem": "O preço está abaixo do preço mínimo (custos mais impostos e taxas)."})
    if margem is not None and lucro >= 0 and margem_desejada is not None and margem < margem_desejada:
        alertas.append({"nivel": "aviso", "codigo": "abaixo_da_margem", "mensagem": f"A margem estimada ({margem}%) é menor que a desejada ({margem_desejada}%)."})
    if desconto and lucro < 0:
        alertas.append({"nivel": "erro", "codigo": "desconto_margem_negativa", "mensagem": "O desconto levou a margem para negativa."})

    return {
        "origem_preco": origem, "metodo_preco": metodo,
        "horas_trabalho": str(horas_trabalho.quantize(Decimal("0.01"))), "horas_deslocamento": str(horas_deslocamento),
        "custo_trabalho": custo_trabalho, "custo_deslocamento": custo_deslocamento, "custos_discriminados": discriminado,
        "custos_incluidos": por_repasse["incluido"], "custos_absorvidos": por_repasse["absorvido"],
        "custo_total": custo_total, "preco_minimo": preco_minimo, "preco_alvo": preco_alvo,
        "preco_base": venda, "acrescimos": acrescimo_pct_valor + acrescimo_valor, "desconto": desconto, "total": total,
        "encargos_pct": str((encargos * CEM).quantize(Decimal("0.01"))), "impostos_taxas": encargos_valor, "lucro": lucro,
        "margem": None if margem is None else str(margem), "markup_resultante": None if markup_resultante is None else str(markup_resultante),
        "margem_desejada": None if margem_desejada is None else str(margem_desejada), "alertas": alertas,
    }
