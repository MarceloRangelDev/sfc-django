"""Cálculo de custo dos processados e motor de recálculo em cascata.

Cadeia de dependência (sempre dentro do MESMO cliente/unidade):

    Insumo A --(item)--> Processado B --(insumo gerado)--> Insumo B --(item)--> Processado C ...

Alterar o custo/unidade de um insumo (ou salvar um processado) recalcula todos
os processados que dependem dele, em qualquer nível, UMA vez cada e em ordem de
dependência (um processado só é calculado depois dos que ele usa). Tudo corre
numa única transação: se algo falhar, nada fica pela metade e a alteração que
disparou o recálculo também é desfeita — rodar de novo é seguro (idempotente),
inclusive pelo comando `manage.py recalcular_custos`.

Pontos de escrita de custo que usam este mecanismo:
- tela e API de Insumo (sinal post_save em apps/processado/apps.py);
- tela de Processado (services.salvar_processado);
- comando `recalcular_custos` (reprocessa tudo).
Novas dependências (ficha técnica, precificação) devem se pendurar em
GANCHOS_POS_RECALCULO, que roda no fim, com os insumos alterados.
"""
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from apps.medida.models import Medida
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import UNIDADE_PADRAO_PRECO, converter, preco_por_unidade_padrao, quantidade_em_base

from .models import Processado, ProcessadoItem

logger = logging.getLogger(__name__)

# Funções `f(insumo_ids, cliente_id)` executadas ao final de cada recálculo,
# dentro da mesma transação (fichas técnicas e precificações entram aqui).
# `insumo_ids=None` significa "todos os registros do cliente" (comando recalcular_custos).
GANCHOS_POS_RECALCULO = []

LIMITE_FATOR = Decimal("10000000")  # cabe no DecimalField(12, 4)


class CicloDeDependenciaError(RegraNegocioError):
    """Uma receita dependeria, direta ou indiretamente, de si mesma."""


# --------------------------------------------------------------------------
# Cálculo de um processado
# --------------------------------------------------------------------------

def custo_do_item(insumo, medida, quantidade):
    """Custo (centavos) de `quantidade` de `medida` do insumo.

    Proporcional ao que foi pago: custo = preço pago x usado / comprado, com a
    conversão de unidades feita na unidade base da dimensão (2 kg == 2000 g).
    Só arredonda no final (ROUND_HALF_UP). Unidades de dimensões diferentes
    (massa x volume) não se convertem sem densidade: é erro, não palpite.
    """
    if medida.dimensao != insumo.medida.dimensao:
        raise RegraNegocioError(
            f'"{insumo.nome}": a unidade {medida.sigla} não é compatível com a unidade de compra '
            f"do insumo ({insumo.medida.sigla})."
        )
    comprada = quantidade_em_base(insumo.quantidade, insumo.medida)
    if comprada == 0:  # nunca divide por zero
        raise RegraNegocioError(f'"{insumo.nome}": a quantidade comprada deve ser maior que zero.')
    usada = quantidade_em_base(quantidade, medida)
    return int((Decimal(insumo.preco_pago) * usada / comprada).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fator_de_rendimento(processado, itens):
    """Rendimento / quantidade usada, ambos na unidade base (500 g de 1 kg -> 0,5).

    Só existe quando TODOS os itens estão na mesma dimensão da saída; senão
    (ex.: litros de leite virando kg de doce) não há comparação honesta e
    devolve None, assim como quando nada foi usado."""
    dimensao = processado.rendimento_medida.dimensao
    if not itens or any(i.medida.dimensao != dimensao for i in itens):
        return None
    usado = sum(quantidade_em_base(i.quantidade, i.medida) for i in itens)
    if usado == 0:
        return None
    fator = quantidade_em_base(processado.rendimento_quantidade, processado.rendimento_medida) / usado
    return fator.quantize(Decimal("0.0001")) if fator < LIMITE_FATOR else None


def medida_maior_grandeza(dimensao):
    """A unidade "de referência" da dimensão (a de preço: Quilo, Litro, Unidade, Metro...),
    que o insumo gerado usa. None se ela estiver inativa/ausente na tabela `medidas`."""
    fator, _sigla = UNIDADE_PADRAO_PRECO[dimensao]
    return Medida.objects.filter(dimensao=dimensao, fator_base=fator, ativo=True).order_by("id").first()


def representacao_do_insumo(processado, insumo):
    """(medida, quantidade, valor em centavos) que o insumo gerado deve ter: 1 unidade
    da maior grandeza (ou a mesma quantidade de referência na unidade do estoque, se
    o insumo já tem saldo). Compartilhada por `_sincronizar_insumo` e pela conferência
    `insumo_gerado_desatualizado`."""
    custo_da_unidade = preco_por_unidade_padrao(processado.custo_total, processado.rendimento_quantidade, processado.rendimento_medida)
    referencia = medida_maior_grandeza(processado.rendimento_medida.dimensao)
    if referencia is None:  # unidade de referência indisponível: mantém o rendimento informado
        return processado.rendimento_medida, processado.rendimento_quantidade, processado.custo_total
    medida, quantidade = referencia, Decimal(1)
    if insumo.pk and insumo.medida_id != referencia.pk:
        from apps.estoque.services import insumo_tem_estoque  # import tardio: evita ciclo entre apps

        if insumo_tem_estoque(insumo):
            medida = insumo.medida
            quantidade = converter(1, referencia, medida, casas=4)
            if quantidade <= 0:
                raise RegraNegocioError("O insumo gerado por este processado tem estoque em uma unidade incompatível com a saída.")
    return medida, quantidade, custo_da_unidade


def insumo_gerado_desatualizado(processado):
    """True se o insumo gerado ainda está no formato antigo (ex.: "500 g por R$ 39,90")
    em vez de "1 kg ao custo do quilo"."""
    insumo = processado.insumo
    if insumo is None:
        return False
    medida, quantidade, valor = representacao_do_insumo(processado, insumo)
    return (insumo.medida_id, insumo.quantidade, insumo.preco_pago) != (medida.pk, quantidade, valor)


def normalizar_insumos_gerados():
    """Leva ao formato "1 unidade da maior grandeza" os insumos gerados que ficaram no
    formato antigo, recalculando só os clientes afetados. Idempotente; chamada após o
    `migrate`. Devolve os ids de clientes recalculados."""
    clientes = {
        p.cliente_id
        for p in Processado.objects.filter(insumo__isnull=False, ativo=True).select_related("insumo", "rendimento_medida", "cliente")
        if insumo_gerado_desatualizado(p)
    }
    for cliente_id in sorted(clientes):
        recalcular_todos(cliente_id)
    return sorted(clientes)


def _sincronizar_insumo(processado):
    """Cria/atualiza o Insumo que representa o processado na lista geral.

    O insumo gerado é SEMPRE de 1 unidade da MAIOR GRANDEZA da dimensão da saída
    (1 kg, 1 L, 1 un, 1 m...) e o valor é o CUSTO dessa unidade: 500 g que custaram
    R$ 39,90 viram "1 kg a R$ 79,80". Assim a lista de insumos não mostra "valor pago"
    de uma receita, só o preço por kg/L/un.
    Exceção segura: se o insumo já tem ESTOQUE, ele mantém a unidade em que o saldo foi
    guardado (a mesma quantidade de referência, ex.: 1000 g) — o valor continua o custo
    de 1 kg e o saldo não muda de significado."""
    from apps.insumo.models import Insumo

    insumo = processado.insumo or Insumo(cliente=processado.cliente)
    medida, quantidade, valor = representacao_do_insumo(processado, insumo)
    insumo.nome = processado.nome
    insumo.medida = medida
    insumo.quantidade = quantidade
    insumo.preco_pago = valor
    insumo.ativo = processado.ativo
    insumo._sem_propagacao = True  # a propagação aos próximos níveis é feita por este módulo
    insumo.save()
    processado.insumo = insumo


def recalcular_processado(processado):
    """Recalcula itens, custo total, fator e o insumo gerado de UM processado
    (não propaga). Os insumos dos itens são lidos do banco neste momento, por
    isso os processados de que ele depende precisam ter sido calculados antes."""
    itens = list(processado.itens.select_related("insumo", "insumo__medida", "medida"))
    total = 0
    for item in itens:
        custo = custo_do_item(item.insumo, item.medida, item.quantidade)
        if item.custo != custo:
            item.custo = custo
            item.save(update_fields=["custo"])
        total += custo
    processado.custo_total = total
    processado.fator_rendimento = fator_de_rendimento(processado, itens)
    _sincronizar_insumo(processado)
    processado.save(update_fields=["custo_total", "fator_rendimento", "insumo", "atualizado_em"])


# --------------------------------------------------------------------------
# Dependências
# --------------------------------------------------------------------------

def _coletar_dependentes(insumo_ids, cliente_id):
    """{id: Processado} de todos os processados ATIVOS do cliente que usam,
    em qualquer nível, os insumos informados. O conjunto "visitado" garante
    parada mesmo com ciclo acidental nos dados (o ciclo é tratado na ordenação)."""
    afetados = {}
    fronteira = set(insumo_ids)
    while fronteira:
        proximos = set()
        consulta = Processado.objects.filter(
            cliente_id=cliente_id, ativo=True, itens__insumo_id__in=fronteira
        ).distinct().select_related("cliente", "rendimento_medida", "insumo")
        for processado in consulta:
            if processado.pk not in afetados:
                afetados[processado.pk] = processado
                if processado.insumo_id:
                    proximos.add(processado.insumo_id)
        fronteira = proximos
    return afetados


def _ordenar_por_dependencia(afetados):
    """Ordem topológica (Kahn): quem é usado vem antes de quem usa. Sobrando
    processados sem ordem possível = ciclo, com erro legível."""
    produtor = {p.insumo_id: p.pk for p in afetados.values() if p.insumo_id}
    depende_de = {pk: set() for pk in afetados}
    uso = ProcessadoItem.objects.filter(processado_id__in=afetados, insumo_id__in=produtor)
    for processado_id, insumo_id in uso.values_list("processado_id", "insumo_id"):
        depende_de[processado_id].add(produtor[insumo_id])

    ordem = []
    prontos = sorted(pk for pk, deps in depende_de.items() if not deps)
    while prontos:
        atual = prontos.pop(0)
        ordem.append(atual)
        for pk, deps in depende_de.items():
            if atual in deps:
                deps.discard(atual)
                if not deps and pk not in ordem and pk not in prontos:
                    prontos.append(pk)
    if len(ordem) != len(afetados):
        nomes = ", ".join(f'"{afetados[pk].nome}"' for pk in afetados if pk not in ordem)
        raise CicloDeDependenciaError(f"Dependência circular entre receitas/processados: {nomes}.")
    return [afetados[pk] for pk in ordem]


def verificar_ciclo(processado, insumo_ids):
    """Impede SALVAR um processado que passaria a depender de si mesmo.
    Sobe pela cadeia: cada insumo usado pode ser a saída de outro processado,
    cujos itens também são verificados. Processado novo não pode estar em ciclo."""
    if processado.pk is None:
        return
    from apps.insumo.models import Insumo

    pendentes = [(iid, iid) for iid in insumo_ids]  # (insumo visitado, insumo usado diretamente)
    visitados = set()
    while pendentes:
        atual, raiz = pendentes.pop()
        if atual in visitados:
            continue
        visitados.add(atual)
        produtor = Processado.objects.filter(insumo_id=atual, cliente_id=processado.cliente_id).first()
        if produtor is None:
            continue
        if produtor.pk == processado.pk:
            nome = Insumo.objects.filter(pk=raiz).values_list("nome", flat=True).first()
            raise CicloDeDependenciaError(
                f'"{processado.nome}" não pode usar "{nome}": esse insumo depende, direta ou '
                f"indiretamente, de \"{processado.nome}\" (dependência circular)."
            )
        pendentes.extend((iid, raiz) for iid in produtor.itens.values_list("insumo_id", flat=True))


def _recalcular_em_ordem(afetados):
    for processado in _ordenar_por_dependencia(afetados):
        recalcular_processado(processado)


def propagar_insumos(insumo_ids, cliente_id):
    """Recalcula tudo o que depende dos insumos alterados, até o último nível,
    e depois roda os ganchos (fichas, precificações). Atômico e idempotente."""
    insumo_ids = {i for i in insumo_ids if i}
    if not insumo_ids:
        return
    try:
        with transaction.atomic():
            afetados = _coletar_dependentes(insumo_ids, cliente_id)
            _recalcular_em_ordem(afetados)
            # Os insumos gerados também mudaram: os ganchos recebem todos eles.
            alterados = insumo_ids | {p.insumo_id for p in afetados.values() if p.insumo_id}
            for gancho in GANCHOS_POS_RECALCULO:
                gancho(alterados, cliente_id)
    except Exception:
        # Sem dados do usuário no log: só ids (ver README, "Recálculo").
        logger.exception("Falha no recálculo de dependências (cliente=%s, insumos=%s)", cliente_id, sorted(insumo_ids))
        raise


def recalcular_todos(cliente_id=None):
    """Reprocessa todos os processados ativos e dispara os ganchos (fichas...)
    para TUDO (de um cliente ou de todos), cada cliente em sua transação.
    Devolve a quantidade de processados recalculados. É a "nova tentativa"
    segura após uma falha ou correção de dados."""
    from apps.cliente.models import Cliente

    consulta = Processado.objects.filter(ativo=True).select_related("cliente", "rendimento_medida", "insumo")
    clientes = Cliente.objects.all()
    if cliente_id:
        consulta = consulta.filter(cliente_id=cliente_id)
        clientes = clientes.filter(pk=cliente_id)
    por_cliente = {}
    for processado in consulta:
        por_cliente.setdefault(processado.cliente_id, {})[processado.pk] = processado
    total = 0
    for cliente in clientes.values_list("pk", flat=True):
        afetados = por_cliente.get(cliente, {})
        try:
            with transaction.atomic():
                _recalcular_em_ordem(afetados)
                for gancho in GANCHOS_POS_RECALCULO:
                    gancho(None, cliente)  # None = todos os registros do cliente
        except Exception:
            logger.exception("Falha ao recalcular o cliente %s", cliente)
            raise
        total += len(afetados)
    return total
