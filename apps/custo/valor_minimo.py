"""Valor MÍNIMO a cobrar por período, derivado do custo operacional (segmentos de serviço).

Ideia: quem presta serviço (ex.: pintor de imóveis) não sabe quanto cobrar por mês, quinzena,
semana, dia ou hora. O sistema calcula o piso — o quanto precisa faturar só para cobrir o custo
operacional — e o usuário soma a margem de lucro por cima. Por isso NÃO há margem aqui.

Fórmulas (centavos, arredondamento meio-para-cima UMA vez por valor) — a mesma convenção da mão
de obra (`apps.maodeobra.calculo.derivados_do_mes`), com `horas_mes` e `dias_mes` da unidade
(`ConfiguracaoMaoDeObra`, padrão 220 h e 30 dias):
    mensal = base;  quinzenal = base ÷ 2;  semanal = base × 7 ÷ dias_mes;
    diário = base ÷ dias_mes;  hora = base ÷ horas_mes.
`base` = custo operacional mensal ativo (+ folha mensal dos colaboradores ativos, se pedido).
"""
from apps.custo.services import total_operacional
from apps.maodeobra.calculo import derivados_do_mes
from apps.maodeobra.models import Colaborador, ConfiguracaoMaoDeObra


def valores_minimos(base_mensal, horas_mes, dias_mes):
    """Função pura. `base_mensal` em centavos → dict com mensal/quinzenal/semanal/diario/hora (centavos).
    Levanta RegraNegocioError se horas/dias forem <= 0 (via derivados_do_mes)."""
    base = int(base_mensal or 0)
    d = derivados_do_mes(base, horas_mes, dias_mes)
    return {"mensal": base, "quinzenal": d["quinzena"], "semanal": d["semana"], "diario": d["dia"], "hora": d["hora"]}


def folha_mensal(cliente):
    """Soma do valor mensal dos colaboradores ativos da unidade (centavos); quem não tem valor mensal não entra."""
    return sum(Colaborador.objects.filter(cliente=cliente, ativo=True, valor_mes__isnull=False).values_list("valor_mes", flat=True))


def atende_servico(cliente):
    """O indicador só vale para unidades cujo segmento precifica serviço (serviço ou ambos)."""
    return bool(cliente.segmento_id and cliente.segmento.precifica_servico)


def valor_minimo_da_unidade(cliente, incluir_mao_de_obra=False):
    """Valores mínimos da unidade. Sempre escopo de UMA unidade (o chamador já validou o acesso).
    Devolve {} (vazio) se o segmento não é de serviço; `sem_custo=True` quando não há custo cadastrado."""
    if not atende_servico(cliente):
        return {}
    config = ConfiguracaoMaoDeObra.da_unidade(cliente)  # cria a linha padrão no primeiro uso
    custo = total_operacional(cliente)
    folha = folha_mensal(cliente) if incluir_mao_de_obra else 0
    base = custo + folha
    return {
        "custo_operacional": custo, "mao_de_obra": folha, "incluiu_mao_de_obra": bool(incluir_mao_de_obra),
        "horas_mes": config.horas_mes, "dias_mes": config.dias_mes, "sem_custo": base == 0,
        "valores": valores_minimos(base, config.horas_mes, config.dias_mes),
    }
