"""Indicadores do painel, calculados a partir dos dados que o sistema já tem.

Regras:
- **Tenant**: tudo sobre UMA unidade (a do usuário; a matriz escolhe uma filial se
  tiver `cliente.view_itensfilial`). Nunca somamos unidades.
- **Permissões**: cada bloco só é calculado/mostrado se o usuário tem a permissão de
  ver o módulo E o módulo está habilitado para ele — o mesmo critério das telas.
- **Período** (`periodo`): vale para os indicadores que dependem de data (resultado do
  período e vencimentos). Os demais mostram a posição de HOJE (saldos, estoque).
Cada indicador reaproveita a função do módulo dono do dado; aqui não há regra nova.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, BigIntegerField, Count, ExpressionWrapper, F, Sum
from django.utils import timezone

from web_project.acesso import modulos_habilitados
from web_project.regras.dinheiro import DinheiroService

PRESETS = [
    ("mes_atual", "Mês atual"), ("mes_anterior", "Mês anterior"), ("30_dias", "Últimos 30 dias"), ("personalizado", "Personalizado"),
]


def _ultimo_dia_do_mes(dia):
    return (dia.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def periodo(preset="mes_atual", inicio=None, fim=None, hoje=None):
    """(início, fim, preset efetivo, rótulo). Período personalizado inválido ou
    incompleto cai no mês atual (nunca levanta erro na tela inicial)."""
    hoje = hoje or timezone.localdate()
    if preset == "mes_anterior":
        fim_mes = hoje.replace(day=1) - timedelta(days=1)
        inicio_mes = fim_mes.replace(day=1)
        return inicio_mes, fim_mes, preset, "Mês anterior"
    if preset == "30_dias":
        return hoje - timedelta(days=29), hoje, preset, "Últimos 30 dias"
    if preset == "personalizado" and inicio and fim and inicio <= fim:
        return inicio, fim, preset, "Personalizado"
    return hoje.replace(day=1), _ultimo_dia_do_mes(hoje), "mes_atual", "Mês atual"


def _reais(centavos):
    return DinheiroService.formatar(centavos or 0)


def _saldo():
    return Sum(ExpressionWrapper(F("valor") - F("valor_baixado"), output_field=BigIntegerField()))


def _media_centavos(valor):
    return int(Decimal(valor).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if valor else 0


def indicadores(usuario, unidade, inicio, fim, hoje=None):
    """Dicionário com os blocos que o usuário pode ver (chave ausente = sem permissão
    ou módulo desabilitado). Valores monetários já formatados em reais."""
    hoje = hoje or timezone.localdate()
    habilitados = modulos_habilitados(usuario)

    def pode(slug, permissao):
        return slug in habilitados and usuario.has_perm(permissao)

    blocos = {}

    if pode("financeiro-relatorios", "financeiro.view_relatoriofinanceiro"):
        from apps.financeiro.services import dre

        r = dre(unidade, inicio, fim, "caixa")
        blocos["resultado"] = {
            "receitas": _reais(r["total_receitas"]), "despesas": _reais(r["total_despesas"]), "resultado": _reais(r["resultado"]),
            "negativo": r["resultado"] < 0, "receitas_centavos": r["total_receitas"], "despesas_centavos": r["total_despesas"],
        }

    from apps.financeiro.models import ContaPagar, ContaReceber

    contas = {}
    for chave, modelo, permissao in (("pagar", ContaPagar, "financeiro.view_contapagar"), ("receber", ContaReceber, "financeiro.view_contareceber")):
        if not pode(f"financeiro-{chave}", permissao):
            continue
        ativas = modelo.objects.filter(cliente=unidade, ativo=True, valor_baixado__lt=F("valor"))
        aberto = ativas.aggregate(qtd=Count("pk"), saldo=_saldo())
        atrasadas = ativas.filter(vencimento__lt=hoje).aggregate(qtd=Count("pk"), saldo=_saldo())
        no_periodo = ativas.filter(vencimento__range=(inicio, fim)).aggregate(qtd=Count("pk"), saldo=_saldo())
        contas[chave] = {
            "aberto_qtd": aberto["qtd"], "aberto": _reais(aberto["saldo"]),
            "atrasadas_qtd": atrasadas["qtd"], "atrasadas": _reais(atrasadas["saldo"]),
            "periodo_qtd": no_periodo["qtd"], "periodo": _reais(no_periodo["saldo"]),
        }
    if contas:
        blocos["contas"] = contas

    if pode("estoque", "estoque.view_movimentoestoque"):
        from apps.estoque.services import alertas_de_estoque_baixo, alertas_de_vencimento, valor_em_estoque

        blocos["estoque"] = {
            "valor": _reais(valor_em_estoque(unidade, hoje)),
            "baixos": alertas_de_estoque_baixo([unidade]).count(),
            "vencendo": alertas_de_vencimento([unidade], hoje).count(),
        }

    cadastros = {}
    from apps.insumo.models import Insumo
    from apps.precificacao.models import Precificacao
    from apps.produto.models import Produto

    for chave, modelo, slug, permissao in (
        ("insumos", Insumo, "insumo", "insumo.view_insumo"),
        ("produtos", Produto, "produto", "produto.view_produto"),
        ("precificacoes", Precificacao, "precificacao", "precificacao.view_precificacao"),
    ):
        if pode(slug, permissao):
            cadastros[chave] = modelo.objects.filter(cliente=unidade, ativo=True).count()
    if cadastros:
        blocos["cadastros"] = cadastros

    if pode("precificacao", "precificacao.view_precificacao"):
        medias = Precificacao.objects.filter(cliente=unidade, ativo=True, preco_venda__gt=0).aggregate(preco=Avg("preco_venda"), cmv=Avg("cmv_percentual"))
        blocos["precificacao"] = {
            "preco_medio": _reais(_media_centavos(medias["preco"])),
            "cmv_medio": f"{Decimal(medias['cmv'] or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}".replace(".", ",") + "%",
            "tem_dados": medias["preco"] is not None,
        }

    # Valor mínimo a cobrar por período (só segmentos de serviço): reaproveita apps.custo.valor_minimo.
    if pode("custo-operacional", "custo.view_custooperacional"):
        from apps.custo.valor_minimo import valor_minimo_da_unidade

        minimo = valor_minimo_da_unidade(unidade)
        if minimo:
            blocos["valor_minimo"] = {
                "sem_custo": minimo["sem_custo"], "horas_mes": minimo["horas_mes"], "dias_mes": minimo["dias_mes"],
                "centavos": minimo["valores"], "valores": {k: _reais(v) for k, v in minimo["valores"].items()},
            }

    if pode("categoria", "categoria.view_categoria"):
        from apps.categoria.calculos import percentual_do_total, ticket_medio_centavos
        from apps.categoria.models import Categoria

        todas = Categoria.objects.filter(cliente=unidade, ativo=True, faturamento__gt=0)
        total = todas.aggregate(t=Sum("faturamento"))["t"] or 0
        blocos["categorias"] = [
            {"nome": c.nome, "faturamento": _reais(c.faturamento), "percentual": percentual_do_total(c.faturamento, total),
             "ticket": _reais(ticket_medio_centavos(c.faturamento, c.quantidade_vendida))}
            for c in todas.order_by("-faturamento", "nome")[:5]
        ]
    return blocos
