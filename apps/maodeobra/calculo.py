"""Cálculo de mão de obra por HORA ou por DIA (funções puras: só Decimal e inteiros; valores em CENTAVOS).

REGRAS (todas configuráveis por unidade em `ConfiguracaoMaoDeObra`)
-------------------------------------------------------------------
Por HORA trabalhada (apontamento com início e fim):
  1. minutos brutos      = fim − início (se o fim é menor que o início, passou da meia-noite: soma 24 h; máximo 24 h);
  2. minutos trabalhados = brutos − pausa (as pausas são DESCONTADAS; a pausa não pode passar do período);
  3. ARREDONDAMENTO dos minutos trabalhados (escolha da unidade):
        `minuto`  — sem arredondar (padrão);
        `quarto`  — para CIMA ao múltiplo de 15 min;
        `hora`    — para CIMA à hora cheia;
  4. jornada normal      = `jornada_horas` × 60 minutos por dia (padrão 8 h). O que passa disso é HORA EXTRA;
  5. valor normal = minutos_normais/60 × valor_hora;
     valor extra  = minutos_extra/60  × valor_hora × (1 + extra_percentual/100)     (padrão 50 %);
  6. DOMINGO: se `adicional_domingo` > 0 (padrão 0 = desligado), TODAS as horas do domingo recebem esse adicional
     percentual sobre o valor_hora (e as horas extras do domingo usam o maior entre o adicional de domingo e o de hora extra);
  7. cada valor é arredondado UMA vez ao centavo (meio para cima); total = normal + extra.

Por DIA trabalhado: valor = dias × valor_dia (`valor_dia` do colaborador; se vazio, valor_hora × jornada_horas). Sem hora extra.

Exemplo (valor_hora R$ 20,00, jornada 8 h, extra 50 %): 9 h 30 min trabalhadas → 8 h × 20 = R$ 160,00 + 1,5 h × 20 × 1,5 = R$ 45,00 = R$ 205,00.
Os resultados guardados num apontamento são um SNAPSHOT: mudar o valor_hora ou a configuração depois NÃO altera lançamentos antigos
(só um recálculo explícito do apontamento).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from web_project.regras import RegraNegocioError

ARRED_MINUTO, ARRED_QUARTO, ARRED_HORA = "minuto", "quarto", "hora"
ARREDONDAMENTOS = [(ARRED_MINUTO, "Por minuto (sem arredondar)"), (ARRED_QUARTO, "Para cima a cada 15 minutos"), (ARRED_HORA, "Para cima à hora cheia")]
MINUTOS_POR_DIA = 24 * 60
LIMITE_CENTAVOS = 10**13


@dataclass(frozen=True)
class ResultadoHoras:
    minutos_brutos: int
    minutos_trabalhados: int  # depois das pausas e do arredondamento
    minutos_normais: int
    minutos_extra: int
    valor_normal: int
    valor_extra: int

    @property
    def valor_total(self):
        return self.valor_normal + self.valor_extra


def _centavos(valor):
    return int(Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def minutos_entre(inicio, fim):
    """Minutos entre dois horários (datetime.time) do mesmo apontamento; fim <= início = virou a noite (+24 h)."""
    a, b = inicio.hour * 60 + inicio.minute, fim.hour * 60 + fim.minute
    minutos = b - a
    if minutos <= 0:
        minutos += MINUTOS_POR_DIA
    return minutos


def arredondar_minutos(minutos, modo):
    if modo == ARRED_MINUTO:
        return minutos
    passo = 15 if modo == ARRED_QUARTO else 60 if modo == ARRED_HORA else None
    if passo is None:
        raise RegraNegocioError("Arredondamento inválido.")
    return -(-minutos // passo) * passo  # teto


def calcular_horas(inicio, fim, pausa_minutos, valor_hora, data, jornada_horas=Decimal("8"), arredondamento=ARRED_MINUTO,
                   extra_percentual=Decimal("50"), adicional_domingo=Decimal("0")):
    """Calcula minutos e valores de UM apontamento por hora (ver o módulo). Levanta RegraNegocioError para entradas inválidas."""
    if valor_hora is None or isinstance(valor_hora, bool) or valor_hora < 0 or valor_hora > LIMITE_CENTAVOS:
        raise RegraNegocioError("Valor da hora inválido.")
    if pausa_minutos < 0:
        raise RegraNegocioError("A pausa não pode ser negativa.")
    brutos = minutos_entre(inicio, fim)
    if pausa_minutos >= brutos:
        raise RegraNegocioError("A pausa deve ser menor que o período trabalhado.")
    trabalhados = arredondar_minutos(brutos - pausa_minutos, arredondamento)
    jornada_min = int((Decimal(jornada_horas) * 60).to_integral_value(rounding=ROUND_HALF_UP))
    normais = min(trabalhados, jornada_min)
    extra = trabalhados - normais
    domingo = (data.weekday() == 6) if isinstance(data, (date, datetime)) else False
    adicional = Decimal(adicional_domingo) if domingo else Decimal("0")
    percentual_normal = adicional
    percentual_extra = max(adicional, Decimal(extra_percentual))
    valor_normal = _centavos(Decimal(normais) / 60 * valor_hora * (1 + percentual_normal / 100))
    valor_extra = _centavos(Decimal(extra) / 60 * valor_hora * (1 + percentual_extra / 100))
    return ResultadoHoras(brutos, trabalhados, normais, extra, valor_normal, valor_extra)


def calcular_dias(dias, valor_dia):
    """Valor de `dias` (Decimal > 0, aceita meio dia) a `valor_dia` centavos."""
    dias = Decimal(dias)
    if dias <= 0 or dias > 31:
        raise RegraNegocioError("Informe de 0,5 a 31 dias.")
    if valor_dia is None or isinstance(valor_dia, bool) or valor_dia < 0 or valor_dia > LIMITE_CENTAVOS:
        raise RegraNegocioError("Valor do dia inválido.")
    return _centavos(dias * valor_dia)


def valor_dia_padrao(valor_hora, jornada_horas):
    """Diária quando o colaborador não tem valor próprio: valor_hora × jornada."""
    return _centavos(Decimal(valor_hora) * Decimal(jornada_horas))


def texto_horas(minutos):
    """'9h30' a partir de minutos."""
    return f"{minutos // 60}h{minutos % 60:02d}"


def primeiro_e_ultimo_dia(ano, mes):
    primeiro = date(ano, mes, 1)
    proximo = (primeiro.replace(day=28) + timedelta(days=4)).replace(day=1)
    return primeiro, proximo - timedelta(days=1)
