"""Composição de custo dos colaboradores, simulação de promoção e indicadores do dashboard (camada de dados sobre custos.py).

Regras de montagem dos itens de um colaborador numa data de referência:

* Vínculo: a função vigente do colaborador na data (`VinculoFuncao`). Sem vínculo, só existe o salário do próprio colaborador
  (`Colaborador.valor_mes`) e a composição sai marcada como INCOMPLETA.
* Itens: os itens ATIVOS da função com vigência cobrindo a data. Se houver mais de um salário base cobrindo a data (dado antigo
  inconsistente), vale o de início mais recente.
* Salário: se o item de salário é personalizável e o colaborador tem `valor_mes`, vale o `valor_mes` (origem "personalizado");
  senão vale o salário da função. Assim o h/mês e o valor da hora/dia do colaborador (que derivam de `valor_mes`) continuam coerentes.
* Demais itens personalizáveis: valor/percentual da `PersonalizacaoColaborador` do vínculo; os não personalizáveis são sempre os da função.

Tudo é filtrado pela unidade (tenant) do colaborador/função/simulação já carregados pelo chamador; este módulo nunca recebe
identificadores soltos do cliente. O resultado é INFORMATIVO: não altera apontamentos, fichas nem precificações.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q

from . import custos
from .models import Colaborador, ComponenteFuncao, ConfiguracaoMaoDeObra, PersonalizacaoColaborador, SimulacaoPromocao, VinculoFuncao


@dataclass
class ComposicaoColaborador:
    colaborador: Colaborador
    data: object
    funcao: object = None
    vinculo: object = None
    composicao: custos.Composicao = None
    incompleto: bool = False
    motivos: list = field(default_factory=list)


def _vigente(data):
    return Q(ativo=True, inicio__lte=data) & (Q(fim__isnull=True) | Q(fim__gte=data))


def _item_do_componente(c, origem="funcao", valor=None, percentual=None):
    """Item calculável a partir de um componente; `valor`/`percentual` sobrepõem o da função (personalização)."""
    personalizado = origem == "personalizado"
    return custos.Item(
        categoria=c.categoria, nome=c.nome, tipo=c.tipo_valor, valor=valor if personalizado and c.tipo_valor == custos.FIXO else c.valor,
        percentual=percentual if personalizado and c.tipo_valor == custos.PERCENTUAL else c.percentual, base=c.base, periodicidade=c.periodicidade,
        chave=c.pk, origem=origem, personalizavel=c.personalizavel, herdado_valor=c.valor if personalizado else None,
        herdado_percentual=c.percentual if personalizado else None,
    )


def _escolher_salario(componentes):
    """Mantém um único componente de salário (o de início mais recente) entre os vigentes."""
    salarios = sorted((c for c in componentes if c.categoria == custos.SALARIO), key=lambda c: c.inicio)
    return [c for c in componentes if c.categoria != custos.SALARIO] + salarios[-1:]


def configuracao_da_unidade(cliente_id, cache):
    if cliente_id not in cache:
        cache[cliente_id] = ConfiguracaoMaoDeObra.objects.get_or_create(cliente_id=cliente_id)[0]
    return cache[cliente_id]


def _montar(colaborador, data, vinculo, componentes, personalizacoes, config):
    """Monta e calcula a composição de UM colaborador com os dados já carregados (sem consultas)."""
    resultado = ComposicaoColaborador(colaborador=colaborador, data=data, vinculo=vinculo, funcao=vinculo.funcao if vinculo else None)
    itens = []
    if vinculo is None:
        resultado.incompleto = True
        resultado.motivos.append("Sem função vinculada nesta data: só o salário do colaborador é considerado.")
        if colaborador.valor_mes:
            itens.append(custos.Item(categoria=custos.SALARIO, nome="Salário do colaborador", valor=colaborador.valor_mes, origem="colaborador"))
    else:
        tem_salario = False
        for c in _escolher_salario(componentes):
            if c.categoria == custos.SALARIO:
                tem_salario = True
                if c.personalizavel and colaborador.valor_mes:
                    itens.append(_item_do_componente(c, "personalizado", valor=colaborador.valor_mes))
                else:
                    itens.append(_item_do_componente(c))
                continue
            p = personalizacoes.get(c.pk) if c.personalizavel else None
            itens.append(_item_do_componente(c, "personalizado", p.valor, p.percentual) if p else _item_do_componente(c))
        if not tem_salario:
            resultado.incompleto = True
            resultado.motivos.append("A função não tem salário base vigente nesta data.")
            if colaborador.valor_mes:
                itens.append(custos.Item(categoria=custos.SALARIO, nome="Salário do colaborador", valor=colaborador.valor_mes, origem="colaborador"))
    resultado.composicao = custos.calcular(itens, config.horas_mes, config.dias_mes)
    if not resultado.composicao.por_categoria[custos.SALARIO] and not resultado.incompleto:
        resultado.incompleto = True
        resultado.motivos.append("Salário base zerado.")
    return resultado


def composicoes_em_lote(colaboradores, data):
    """Composição de vários colaboradores na data, com 4 consultas no total (sem N+1). Devolve {colaborador.pk: ComposicaoColaborador}."""
    colaboradores = list(colaboradores)
    vinculos = {}
    for v in VinculoFuncao.objects.filter(_vigente_vinculo(data), colaborador__in=colaboradores).select_related("funcao").order_by("inicio"):
        vinculos[v.colaborador_id] = v  # o de início mais recente prevalece
    componentes = defaultdict(list)
    funcoes = {v.funcao_id for v in vinculos.values()}
    for c in ComponenteFuncao.objects.filter(_vigente(data), funcao_id__in=funcoes):
        componentes[c.funcao_id].append(c)
    personalizacoes = defaultdict(dict)
    for p in PersonalizacaoColaborador.objects.filter(vinculo__in=[v.pk for v in vinculos.values()]):
        personalizacoes[p.vinculo_id][p.componente_id] = p
    configs = {}
    resultado = {}
    for colaborador in colaboradores:
        vinculo = vinculos.get(colaborador.pk)
        config = configuracao_da_unidade(colaborador.cliente_id, configs)
        resultado[colaborador.pk] = _montar(
            colaborador, data, vinculo, componentes.get(vinculo.funcao_id, []) if vinculo else [], personalizacoes.get(vinculo.pk, {}) if vinculo else {}, config,
        )
    return resultado


def _vigente_vinculo(data):
    return Q(inicio__lte=data) & (Q(fim__isnull=True) | Q(fim__gte=data))


def composicao_do_colaborador(colaborador, data):
    return composicoes_em_lote([colaborador], data)[colaborador.pk]


def composicao_da_funcao(funcao, data):
    """Composição de referência da função (como seria um colaborador sem personalização e sem salário próprio)."""
    config = ConfiguracaoMaoDeObra.objects.get_or_create(cliente_id=funcao.cliente_id)[0]
    componentes = list(ComponenteFuncao.objects.filter(_vigente(data), funcao=funcao))
    resultado = ComposicaoColaborador(colaborador=None, data=data, funcao=funcao)
    itens = [_item_do_componente(c) for c in _escolher_salario(componentes)]
    resultado.composicao = custos.calcular(itens, config.horas_mes, config.dias_mes)
    if not resultado.composicao.por_categoria[custos.SALARIO]:
        resultado.incompleto = True
        resultado.motivos.append("A função não tem salário base vigente nesta data.")
    return resultado


def historico_de_vinculos(colaborador):
    return list(colaborador.vinculos_funcao.select_related("funcao").order_by("-inicio", "-pk"))


def trocar_funcao(colaborador, funcao, inicio, usuario=None):
    """Vincula `funcao` ao colaborador a partir de `inicio`. Se já havia vínculo aberto com OUTRA função, ele é ENCERRADO no dia
    anterior (histórico preservado; personalizações ficam no vínculo antigo e não passam ao novo). Mesma função = nada muda.
    Devolve (vinculo_atual, mudou)."""
    aberto = colaborador.vinculos_funcao.filter(fim__isnull=True).first()
    if aberto and aberto.funcao_id == funcao.pk:
        return aberto, False
    if aberto:
        if inicio <= aberto.inicio:  # início anterior ao do vínculo atual: a troca "passaria por cima" do histórico
            raise ValueError("A nova vigência deve começar depois do início da função atual.")
        aberto.fim = inicio - timedelta(days=1)
        aberto.save(update_fields=["fim"])
    vinculo = VinculoFuncao(colaborador=colaborador, funcao=funcao, inicio=inicio, criado_por=usuario)
    vinculo.full_clean()
    vinculo.save()
    return vinculo, True


# --------------------------------------------------------------------------
# Simulação de promoção
# --------------------------------------------------------------------------

def _itens_simulados(atual, funcao_destino, simulacao, data, config):
    """Itens do cenário para UM colaborador. Mantém as personalizações só quando a função não muda; função diferente herda
    tudo da função de destino. O salário segue: novo salário > ajuste % sobre o salário atual > salário da função (destino)."""
    colaborador = atual.colaborador
    mesma_funcao = funcao_destino is None or (atual.funcao is not None and funcao_destino.pk == atual.funcao.pk)
    salario_atual = atual.composicao.por_categoria[custos.SALARIO]
    if mesma_funcao:
        itens = [_copiar(i) for i in atual.composicao.itens]
    else:
        componentes = list(ComponenteFuncao.objects.filter(_vigente(data), funcao=funcao_destino))
        itens = [_item_do_componente(c) for c in _escolher_salario(componentes)]
    if simulacao.novo_salario is not None:
        novo = simulacao.novo_salario
    elif simulacao.ajuste_salario_percentual is not None:
        novo = int((Decimal(salario_atual) * (1 + simulacao.ajuste_salario_percentual / 100)).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
    else:
        novo = None
    if novo is not None:
        itens = [i for i in itens if i.categoria != custos.SALARIO]
        itens.insert(0, custos.Item(categoria=custos.SALARIO, nome="Salário (simulado)", valor=novo, origem="personalizado", herdado_valor=salario_atual))
    elif not any(i.categoria == custos.SALARIO for i in itens) and colaborador.valor_mes:
        itens.insert(0, custos.Item(categoria=custos.SALARIO, nome="Salário do colaborador", valor=colaborador.valor_mes, origem="colaborador"))
    return custos.calcular(itens, config.horas_mes, config.dias_mes)


def _copiar(item):
    return custos.Item(
        categoria=item.categoria, nome=item.nome, tipo=item.tipo, valor=item.valor, percentual=item.percentual, base=item.base, periodicidade=item.periodicidade,
        chave=item.chave, origem=item.origem, personalizavel=item.personalizavel, herdado_valor=item.herdado_valor, herdado_percentual=item.herdado_percentual,
    )


def simular(simulacao):
    """Resultado de uma `SimulacaoPromocao` (somente leitura): lista por colaborador afetado e totais, atual × simulado.
    Considera apenas colaboradores ATIVOS e da unidade da simulação (a pesquisa já é filtrada pela unidade)."""
    data = simulacao.data_referencia
    if simulacao.tipo == SimulacaoPromocao.PESSOA:
        colaboradores = list(Colaborador.objects.filter(pk=simulacao.colaborador_id, cliente_id=simulacao.cliente_id))
    else:
        ids = VinculoFuncao.objects.filter(_vigente_vinculo(data), funcao_id=simulacao.funcao_origem_id, funcao__cliente_id=simulacao.cliente_id).values_list("colaborador_id", flat=True)
        colaboradores = list(Colaborador.objects.filter(pk__in=ids, cliente_id=simulacao.cliente_id, ativo=True).order_by("nome"))
    atuais = composicoes_em_lote(colaboradores, data)
    configs = {}
    linhas = []
    total_atual = total_simulado = 0
    for colaborador in colaboradores:
        atual = atuais[colaborador.pk]
        config = configuracao_da_unidade(colaborador.cliente_id, configs)
        simulado = _itens_simulados(atual, simulacao.funcao_destino, simulacao, data, config)
        periodos = []
        for codigo, rotulo in custos.PERIODOS:
            antes, depois = atual.composicao.periodos[codigo], simulado.periodos[codigo]
            diferenca, percentual = custos.variacao(antes, depois)
            periodos.append({"codigo": codigo, "rotulo": rotulo, "atual": antes, "simulado": depois, "diferenca": diferenca, "percentual": percentual})
        linhas.append({"colaborador": colaborador, "atual": atual, "simulado": simulado, "periodos": periodos})
        total_atual += atual.composicao.total_mensal
        total_simulado += simulado.total_mensal
    diferenca, percentual = custos.variacao(total_atual, total_simulado)
    # Totais por período = soma dos períodos de cada colaborador (cada unidade pode ter seus próprios dias do mês).
    tabela = []
    for indice, (codigo, rotulo) in enumerate(custos.PERIODOS):
        antes, depois = sum(l["periodos"][indice]["atual"] for l in linhas), sum(l["periodos"][indice]["simulado"] for l in linhas)
        delta, pct = custos.variacao(antes, depois)
        tabela.append({"codigo": codigo, "rotulo": rotulo, "atual": antes, "simulado": depois, "diferenca": delta, "percentual": pct})
    return {
        "linhas": linhas, "quantidade": len(linhas), "total_mensal_atual": total_atual, "total_mensal_simulado": total_simulado,
        "diferenca_mensal": diferenca, "percentual": percentual, "periodos": tabela,
        "incompletos": sum(1 for l in linhas if l["atual"].incompleto),
    }


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def indicadores(colaboradores, data, data_anterior=None):
    """Indicadores sobre os colaboradores ATIVOS recebidos (o chamador já filtrou pelo escopo de unidades e filtros da tela).
    Compara com `data_anterior` quando informada. Colaboradores sem função ou com função sem salário entram na contagem
    de `incompletos` e o painel avisa que o total considera dados parciais."""
    colaboradores = list(colaboradores)
    composicoes = composicoes_em_lote(colaboradores, data)
    por_funcao = defaultdict(lambda: {"quantidade": 0, "total": 0})
    por_categoria = {c: 0 for c in custos.ORDEM}
    periodos = {codigo: 0 for codigo, _ in custos.PERIODOS}  # soma dos períodos de cada colaborador (cada unidade tem seus dias do mês)
    total = 0
    incompletos = 0
    for c in colaboradores:
        r = composicoes[c.pk]
        nome = r.funcao.nome if r.funcao else "Sem função"
        por_funcao[nome]["quantidade"] += 1
        por_funcao[nome]["total"] += r.composicao.total_mensal
        for categoria, valor in r.composicao.por_categoria.items():
            por_categoria[categoria] += valor
        total += r.composicao.total_mensal
        for codigo, valor in r.composicao.periodos.items():
            periodos[codigo] += valor
        incompletos += 1 if r.incompleto else 0
    quantidade = len(colaboradores)
    funcoes = [
        {"funcao": nome, "quantidade": d["quantidade"], "total_mensal": d["total"], "medio_mensal": round(d["total"] / d["quantidade"]) if d["quantidade"] else 0}
        for nome, d in sorted(por_funcao.items(), key=lambda x: (-x[1]["total"], x[0]))
    ]
    resultado = {
        "data": data, "quantidade": quantidade, "total_mensal": total, "medio_mensal": round(total / quantidade) if quantidade else 0,
        "por_funcao": funcoes, "por_categoria": por_categoria, "incompletos": incompletos,
        "periodos": periodos, "medio_periodos": {codigo: round(valor / quantidade) if quantidade else 0 for codigo, valor in periodos.items()},
        "periodos_lista": [
            {"codigo": codigo, "rotulo": rotulo, "total": periodos[codigo], "medio": round(periodos[codigo] / quantidade) if quantidade else 0} for codigo, rotulo in custos.PERIODOS
        ],
        "categorias": [
            {"codigo": codigo, "rotulo": rotulo, "valor": por_categoria[codigo], "percentual": round(por_categoria[codigo] * 100 / total, 1) if total else 0} for codigo, rotulo in custos.CATEGORIAS
        ],
    }
    if data_anterior:
        anterior = indicadores(colaboradores, data_anterior)
        diferenca, percentual = custos.variacao(anterior["total_mensal"], total)
        resultado["comparacao"] = {"data": data_anterior, "total_mensal": anterior["total_mensal"], "diferenca": diferenca, "percentual": percentual, "quantidade": anterior["quantidade"]}
    return resultado


# --------------------------------------------------------------------------
# Apoio compartilhado por telas e API
# --------------------------------------------------------------------------

def encerrar_ou_excluir_componente(request, item):
    """Item que ainda NÃO entrou em vigor e sem personalização é apagado; os demais são ENCERRADOS (fim = ontem), para o passado
    continuar calculável. Auditado. Devolve "excluido" ou "encerrado". Mesma regra para tela e API."""
    from django.utils import timezone

    from apps.auditoria.utils import registrar_edicao, registrar_exclusao, tirar_foto

    hoje = timezone.localdate()
    if item.inicio > hoje and not item.personalizacoes.exists():
        registrar_exclusao(request, item)
        item.delete()
        return "excluido"
    foto = tirar_foto(item)
    if item.fim is None or item.fim >= hoje:
        item.fim = max(item.inicio, hoje - timedelta(days=1))
        item.save(update_fields=["fim", "atualizado_em"])
        registrar_edicao(request, item, foto)
    return "encerrado"


def _texto(valor):
    return None if valor is None else str(valor)


def como_dict(resultado):
    """Composição de um colaborador/função como dicionário simples (JSON da API): dinheiro em centavos, percentuais em texto."""
    composicao = resultado.composicao
    return {
        "colaborador": resultado.colaborador.pk if resultado.colaborador else None,
        "funcao": {"id": resultado.funcao.pk, "nome": resultado.funcao.nome} if resultado.funcao else None,
        "data": resultado.data.isoformat(), "incompleto": resultado.incompleto, "motivos": resultado.motivos + composicao.avisos,
        "itens": [
            {
                "componente": i.chave, "categoria": i.categoria, "nome": i.nome, "tipo_valor": i.tipo, "valor": i.valor, "percentual": _texto(i.percentual), "base": i.base,
                "periodicidade": i.periodicidade, "origem": i.origem, "personalizavel": i.personalizavel, "valor_herdado": i.herdado_valor,
                "percentual_herdado": _texto(i.herdado_percentual), "valor_mensal": i.mensal,
            }
            for i in composicao.itens
        ],
        "por_categoria": composicao.por_categoria, "total_mensal": composicao.total_mensal, "periodos": composicao.periodos, "custo_hora": composicao.custo_hora,
    }
