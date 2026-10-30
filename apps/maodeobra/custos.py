"""Custo COMPLETO de um colaborador a partir dos itens da função (funções puras: só Decimal e inteiros; valores em CENTAVOS).

Este módulo é informativo: NÃO altera o valor da hora/dia usado nos apontamentos (ver calculo.py e `Colaborador.valor_hora`).

COMPOSIÇÃO MENSAL
-----------------
Cada item é de uma categoria e é calculado NESTA ordem, para que nenhuma base seja circular nem um item entre duas vezes numa base:

    salario → adicional → dsr → provisao → encargo → beneficio → indireto

Item FIXO: valor em centavos; periodicidade `anual` é dividida por 12 (valor mensal = anual ÷ 12).
Item PERCENTUAL: percentual × BASE, onde o percentual é a taxa MENSAL aplicada à base mensal. Bases disponíveis:

    salario                = salário base
    salario_adicionais     = salário + adicionais
    remuneracao            = salário + adicionais + DSR
    remuneracao_provisoes  = remuneração + provisões

Bases aceitas por categoria (`BASES_POR_CATEGORIA`): quem é calculado antes pode servir de base para quem vem depois; nunca o contrário.
Cada item é arredondado UMA vez ao centavo (meio para cima); os totais são somas de itens já arredondados.

PERÍODOS (a partir do total MENSAL, com os dias do mês configurados na unidade; padrão 30)
    dia = mês ÷ dias_mes · semana = mês × 7 ÷ dias_mes · quinzena = mês ÷ 2 · mês · bimestre = 2 × mês ·
    trimestre = 3 × mês · semestre = 6 × mês · ano = 12 × mês
Cada período é arredondado uma vez a partir do mensal (não da soma de períodos menores). Os três primeiros são os mesmos de
`calculo.derivados_do_mes`. Custo por hora = mês ÷ horas_mes (h/mês da unidade).
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from web_project.regras import RegraNegocioError

SALARIO, ADICIONAL, DSR, PROVISAO, ENCARGO, BENEFICIO, INDIRETO = "salario", "adicional", "dsr", "provisao", "encargo", "beneficio", "indireto"
CATEGORIAS = [
    (SALARIO, "Salário base"), (ADICIONAL, "Adicional"), (DSR, "DSR"), (PROVISAO, "Provisão mensal"),
    (ENCARGO, "Encargo"), (BENEFICIO, "Benefício"), (INDIRETO, "Custo indireto"),
]
ORDEM = [codigo for codigo, _ in CATEGORIAS]
ROTULO_CATEGORIA = dict(CATEGORIAS)

FIXO, PERCENTUAL = "fixo", "percentual"
TIPOS_VALOR = [(FIXO, "Valor fixo"), (PERCENTUAL, "Percentual sobre uma base")]
MENSAL, ANUAL = "mensal", "anual"
PERIODICIDADES = [(MENSAL, "Por mês"), (ANUAL, "Por ano (÷ 12)")]

B_SALARIO, B_SAL_ADIC, B_REMUNERACAO, B_REM_PROV = "salario", "salario_adicionais", "remuneracao", "remuneracao_provisoes"
BASES = [(B_SALARIO, "Salário base"), (B_SAL_ADIC, "Salário + adicionais"), (B_REMUNERACAO, "Salário + adicionais + DSR"), (B_REM_PROV, "Remuneração + provisões")]
BASES_POR_CATEGORIA = {
    ADICIONAL: (B_SALARIO,),
    DSR: (B_SALARIO, B_SAL_ADIC),
    PROVISAO: (B_SALARIO, B_REMUNERACAO),
    ENCARGO: (B_SALARIO, B_REMUNERACAO, B_REM_PROV),
    BENEFICIO: (B_SALARIO, B_REMUNERACAO),
    INDIRETO: (B_SALARIO, B_REMUNERACAO),
}

PERIODOS = [("dia", "Dia"), ("semana", "Semana"), ("quinzena", "Quinzena"), ("mes", "Mês"), ("bimestre", "Bimestre"), ("trimestre", "Trimestre"), ("semestre", "Semestre"), ("ano", "Ano")]
LIMITE_CENTAVOS = 10**13
LIMITE_PERCENTUAL = Decimal("1000")


@dataclass
class Item:
    """Um item da composição. `herdado_*` guarda o valor da função quando o colaborador o personalizou (para exibir os dois)."""

    categoria: str
    nome: str
    tipo: str = FIXO
    valor: int | None = None  # centavos (tipo fixo)
    percentual: Decimal | None = None  # tipo percentual
    base: str = B_SALARIO
    periodicidade: str = MENSAL
    chave: object = None  # id do componente na função (ou None)
    origem: str = "funcao"  # funcao | personalizado | colaborador
    personalizavel: bool = False
    herdado_valor: int | None = None
    herdado_percentual: Decimal | None = None
    mensal: int = 0  # preenchido por `calcular`

    @property
    def rotulo_categoria(self):
        return ROTULO_CATEGORIA[self.categoria]


@dataclass
class Composicao:
    itens: list = field(default_factory=list)
    por_categoria: dict = field(default_factory=dict)
    total_mensal: int = 0
    periodos: dict = field(default_factory=dict)
    custo_hora: int = 0
    avisos: list = field(default_factory=list)

    @property
    def periodos_lista(self):
        """[(codigo, rotulo, centavos)] na ordem de exibição (para telas)."""
        return [(codigo, rotulo, self.periodos[codigo]) for codigo, rotulo in PERIODOS]

    @property
    def categorias_lista(self):
        """[(codigo, rotulo, centavos mensais)] na ordem de cálculo."""
        return [(codigo, rotulo, self.por_categoria[codigo]) for codigo, rotulo in CATEGORIAS]


def _centavos(valor):
    return int(Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validar_item(categoria, tipo, valor, percentual, base, periodicidade):
    """Regras de um item (usadas pelo modelo, pela tela e pela API). Levanta RegraNegocioError."""
    if categoria not in ORDEM:
        raise RegraNegocioError("Categoria inválida.")
    if tipo == FIXO:
        if valor is None or percentual is not None:
            raise RegraNegocioError("Item de valor fixo: informe o valor e deixe o percentual vazio.")
        if valor < 0 or valor > LIMITE_CENTAVOS:
            raise RegraNegocioError("Valor fora do limite permitido.")
        if periodicidade not in (MENSAL, ANUAL):
            raise RegraNegocioError("Periodicidade inválida.")
    elif tipo == PERCENTUAL:
        if categoria == SALARIO:
            raise RegraNegocioError("O salário base deve ser um valor fixo.")
        if percentual is None or valor is not None:
            raise RegraNegocioError("Item percentual: informe o percentual e deixe o valor vazio.")
        if percentual < 0 or percentual > LIMITE_PERCENTUAL:
            raise RegraNegocioError("Percentual fora do limite permitido.")
        if base not in BASES_POR_CATEGORIA[categoria]:
            permitidas = ", ".join(dict(BASES)[b] for b in BASES_POR_CATEGORIA[categoria])
            raise RegraNegocioError(f"Base inválida para {ROTULO_CATEGORIA[categoria].lower()}. Use: {permitidas}.")
    else:
        raise RegraNegocioError("Tipo de valor inválido.")
    if categoria == SALARIO and periodicidade != MENSAL:
        raise RegraNegocioError("O salário base é sempre mensal.")


def periodos_do_mes(mensal, dias_mes):
    """Valor mensal (centavos) -> {periodo: centavos} para os oito períodos (ver o módulo)."""
    dias = Decimal(dias_mes)
    if dias <= 0:
        raise RegraNegocioError("Os dias do mês devem ser maiores que zero.")
    mes = Decimal(int(mensal))
    return {
        "dia": _centavos(mes / dias), "semana": _centavos(mes * 7 / dias), "quinzena": _centavos(mes / 2), "mes": int(mensal),
        "bimestre": int(mensal) * 2, "trimestre": int(mensal) * 3, "semestre": int(mensal) * 6, "ano": int(mensal) * 12,
    }


def calcular(itens, horas_mes, dias_mes):
    """Calcula a composição mensal, os totais por categoria e os períodos. `itens` é uma lista de `Item` (modificados in loco)."""
    horas = Decimal(horas_mes)
    if horas <= 0:
        raise RegraNegocioError("As horas do mês devem ser maiores que zero.")
    por_categoria = {codigo: 0 for codigo in ORDEM}
    bases = {B_SALARIO: 0, B_SAL_ADIC: 0, B_REMUNERACAO: 0, B_REM_PROV: 0}
    avisos = []

    def atualizar_bases():
        salario = por_categoria[SALARIO]
        bases[B_SALARIO] = salario
        bases[B_SAL_ADIC] = salario + por_categoria[ADICIONAL]
        bases[B_REMUNERACAO] = bases[B_SAL_ADIC] + por_categoria[DSR]
        bases[B_REM_PROV] = bases[B_REMUNERACAO] + por_categoria[PROVISAO]

    for categoria in ORDEM:
        atualizar_bases()  # as bases usam só categorias já fechadas; dentro da categoria elas não mudam
        for item in (i for i in itens if i.categoria == categoria):
            validar_item(item.categoria, item.tipo, item.valor, item.percentual, item.base, item.periodicidade)
            if item.tipo == FIXO:
                item.mensal = _centavos(Decimal(item.valor) / 12) if item.periodicidade == ANUAL else int(item.valor)
            else:
                item.mensal = _centavos(Decimal(bases[item.base]) * Decimal(item.percentual) / 100)
            por_categoria[categoria] += item.mensal
    if not por_categoria[SALARIO]:
        avisos.append("Sem salário base: os percentuais ficam sem base e o custo está incompleto.")
    total = sum(por_categoria.values())
    return Composicao(
        itens=list(itens), por_categoria=por_categoria, total_mensal=total, periodos=periodos_do_mes(total, dias_mes),
        custo_hora=_centavos(Decimal(total) / horas), avisos=avisos,
    )


def variacao(antes, depois):
    """(diferença absoluta em centavos, diferença percentual com 2 casas ou None quando a base é zero)."""
    diferenca = int(depois) - int(antes)
    if not antes:
        return diferenca, None
    return diferenca, (Decimal(diferenca) * 100 / Decimal(int(antes))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
