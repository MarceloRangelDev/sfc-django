"""Regras do financeiro: baixas, estornos, DRE e balanço gerenciais e alertas
de vencimento.

Todos os valores em CENTAVOS (int). Os relatórios são GERENCIAIS: não substituem
as demonstrações contábeis oficiais (ver README).
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import BigIntegerField, Case, F, Q, Sum, When
from django.urls import reverse
from django.utils import timezone

from web_project.regras import RegraNegocioError

from .models import (
    TIPO_DESPESA,
    TIPO_RECEITA,
    AlertaFinanceiro,
    BaixaBase,
    BaixaPagar,
    BaixaReceber,
    ContaPagar,
    ContaReceber,
)

logger = logging.getLogger(__name__)

MAX_ITENS_NO_ALERTA = 10

# "pagar"/"receber" -> (modelo da conta, modelo da baixa, permissão de ver, tipo de categoria)
CONFIG = {
    "pagar": (ContaPagar, BaixaPagar, "financeiro.view_contapagar", TIPO_DESPESA),
    "receber": (ContaReceber, BaixaReceber, "financeiro.view_contareceber", TIPO_RECEITA),
}


def dias_alerta_vencimento():
    return getattr(settings, "FINANCEIRO_DIAS_ALERTA_VENCIMENTO", 3)


def _nome_do_responsavel(usuario):
    return (usuario.get_full_name() or usuario.get_username())[:150] if usuario else ""


# --------------------------------------------------------------------------
# Baixas e estornos
# --------------------------------------------------------------------------

def registrar_baixa(usuario, conta, valor, data=None, observacao=""):
    """Baixa (pagamento/recebimento, total ou parcial) de `valor` centavos.
    A conta é relida sob bloqueio de linha: duas baixas simultâneas não passam
    do saldo (o banco também recusa `valor_baixado` > `valor`). Devolve a baixa."""
    hoje = timezone.localdate()
    data = data or hoje
    if valor is None or valor <= 0:
        raise RegraNegocioError("Informe um valor maior que zero.")
    if data > hoje:
        raise RegraNegocioError("A data da baixa não pode ser futura.")
    modelo, modelo_baixa, _, _ = _config_da_conta(conta)
    with transaction.atomic():
        conta = modelo.objects.select_for_update().get(pk=conta.pk)
        if not conta.ativo:
            raise RegraNegocioError("A conta está cancelada.")
        if valor > conta.saldo:
            raise RegraNegocioError(f"O valor excede o saldo da conta ({_reais(conta.saldo)}).")
        conta.valor_baixado += valor
        conta.save(update_fields=["valor_baixado", "atualizado_em"])
        return modelo_baixa.objects.create(
            conta=conta, tipo=BaixaBase.BAIXA, valor=valor, data=data, observacao=observacao[:255],
            responsavel=usuario, responsavel_nome=_nome_do_responsavel(usuario),
        )


def estornar_baixa(usuario, baixa, observacao=""):
    """Desfaz uma baixa por inteiro criando um ESTORNO (a baixa original continua
    no histórico). Cada baixa é estornada no máximo uma vez."""
    if baixa.tipo != BaixaBase.BAIXA:
        raise RegraNegocioError("Só é possível estornar uma baixa.")
    modelo, modelo_baixa, _, _ = _config_da_conta(baixa.conta)
    with transaction.atomic():
        conta = modelo.objects.select_for_update().get(pk=baixa.conta_id)
        if modelo_baixa.objects.filter(estorno_de=baixa).exists():
            raise RegraNegocioError("Esta baixa já foi estornada.")
        if not conta.ativo:
            raise RegraNegocioError("A conta está cancelada.")
        conta.valor_baixado -= baixa.valor
        conta.save(update_fields=["valor_baixado", "atualizado_em"])
        return modelo_baixa.objects.create(
            conta=conta, tipo=BaixaBase.ESTORNO, valor=baixa.valor, data=timezone.localdate(), estorno_de=baixa,
            observacao=(observacao or f"Estorno da baixa #{baixa.pk}")[:255],
            responsavel=usuario, responsavel_nome=_nome_do_responsavel(usuario),
        )


def _config_da_conta(conta):
    for modelo, modelo_baixa, perm, tipo in CONFIG.values():
        if isinstance(conta, modelo):
            return modelo, modelo_baixa, perm, tipo
    raise TypeError("Conta desconhecida.")


def _reais(centavos):
    from web_project.regras.dinheiro import DinheiroService

    return DinheiroService.formatar(centavos)


# --------------------------------------------------------------------------
# Relatórios gerenciais
# --------------------------------------------------------------------------

def _liquido():
    """Soma de baixas menos estornos."""
    return Sum(Case(When(tipo=BaixaBase.BAIXA, then=F("valor")), default=-F("valor"), output_field=BigIntegerField()))


def _por_categoria(consulta, campo_categoria, soma):
    linhas = [(nome, total or 0) for nome, total in consulta.values_list(campo_categoria).annotate(total=soma).order_by(campo_categoria)]
    return [(nome, total) for nome, total in linhas if total]


def dre(cliente, inicio, fim, regime="caixa"):
    """DRE gerencial de uma unidade no período [inicio, fim].

    - regime `caixa`: receitas = recebimentos líquidos (baixas − estornos) com data no período;
      despesas = pagamentos líquidos no período, cada um na categoria da conta.
    - regime `vencimento`: receitas/despesas = valor das contas ATIVAS com vencimento no período
      (inclui o que ainda não foi pago/recebido).
    Resultado = receitas − despesas. Categorias sem movimento não aparecem."""
    if inicio > fim:
        raise RegraNegocioError("A data inicial não pode ser posterior à final.")
    if regime == "vencimento":
        receitas = _por_categoria(ContaReceber.objects.filter(cliente=cliente, ativo=True, vencimento__range=(inicio, fim)), "categoria__nome", Sum("valor"))
        despesas = _por_categoria(ContaPagar.objects.filter(cliente=cliente, ativo=True, vencimento__range=(inicio, fim)), "categoria__nome", Sum("valor"))
    else:
        receitas = _por_categoria(BaixaReceber.objects.filter(conta__cliente=cliente, data__range=(inicio, fim)), "conta__categoria__nome", _liquido())
        despesas = _por_categoria(BaixaPagar.objects.filter(conta__cliente=cliente, data__range=(inicio, fim)), "conta__categoria__nome", _liquido())
    total_receitas, total_despesas = sum(t for _, t in receitas), sum(t for _, t in despesas)
    return {
        "regime": "vencimento" if regime == "vencimento" else "caixa", "inicio": inicio, "fim": fim,
        "receitas": receitas, "despesas": despesas, "total_receitas": total_receitas, "total_despesas": total_despesas,
        "resultado": total_receitas - total_despesas,
    }


def _em_aberto(modelo, modelo_baixa, cliente, data):
    """Saldo a receber/pagar na data: valor das contas ativas emitidas até ela
    menos as baixas líquidas (até ela) dessas mesmas contas."""
    contas = modelo.objects.filter(cliente=cliente, ativo=True, emissao__lte=data)
    valor = contas.aggregate(t=Sum("valor"))["t"] or 0
    baixado = modelo_baixa.objects.filter(conta__in=contas, data__lte=data).aggregate(t=_liquido())["t"] or 0
    return valor - baixado


def balanco(cliente, data):
    """Quadro patrimonial GERENCIAL simplificado de uma unidade em `data`.

    Ativo = caixa gerencial (recebimentos − pagamentos acumulados, SEM saldo inicial)
          + contas a receber em aberto + estoque a custo.
    Passivo = contas a pagar em aberto.
    Patrimônio (resíduo) = ativo − passivo; inclui capital e saldos iniciais não lançados."""
    from apps.estoque.services import valor_em_estoque

    recebido = BaixaReceber.objects.filter(conta__cliente=cliente, data__lte=data).aggregate(t=_liquido())["t"] or 0
    pago = BaixaPagar.objects.filter(conta__cliente=cliente, data__lte=data).aggregate(t=_liquido())["t"] or 0
    caixa = recebido - pago
    a_receber = _em_aberto(ContaReceber, BaixaReceber, cliente, data)
    a_pagar = _em_aberto(ContaPagar, BaixaPagar, cliente, data)
    estoque = valor_em_estoque(cliente, data)
    ativo = caixa + a_receber + estoque
    return {
        "data": data, "caixa": caixa, "a_receber": a_receber, "estoque": estoque, "ativo": ativo,
        "a_pagar": a_pagar, "passivo": a_pagar, "patrimonio": ativo - a_pagar,
    }


# --------------------------------------------------------------------------
# Alertas de vencimento
# --------------------------------------------------------------------------

def contas_a_vencer(modelo, clientes, hoje=None, dias=None):
    """Contas ativas com saldo que vencem de hoje até hoje + `dias` (padrão: configuração)."""
    hoje = hoje or timezone.localdate()
    limite = hoje + timedelta(days=dias_alerta_vencimento() if dias is None else dias)
    return modelo.objects.filter(cliente__in=clientes, ativo=True, valor_baixado__lt=F("valor"), vencimento__range=(hoje, limite)).select_related("cliente", "categoria").order_by("vencimento")


def contas_atrasadas(modelo, clientes, hoje=None):
    """Contas ativas com saldo já vencidas."""
    hoje = hoje or timezone.localdate()
    return modelo.objects.filter(cliente__in=clientes, ativo=True, valor_baixado__lt=F("valor"), vencimento__lt=hoje).select_related("cliente", "categoria").order_by("vencimento")


def _destinatarios(cliente, permissao):
    from django.contrib.auth.models import User

    candidatos = User.objects.filter(is_active=True, profile__cliente=cliente).select_related("profile")
    return [u for u in candidatos if u.has_perm(permissao)]


def _resumo(contas, situacao):
    itens = [f"{c.descricao} ({c.vencimento.strftime('%d/%m')}, {_reais(c.saldo)})" for c in contas]
    extra = f" e mais {len(itens) - MAX_ITENS_NO_ALERTA}" if len(itens) > MAX_ITENS_NO_ALERTA else ""
    return f"{len(itens)} conta(s) {situacao}: {', '.join(itens[:MAX_ITENS_NO_ALERTA])}{extra}."


def gerar_alertas(clientes):
    """Notifica, por unidade, as contas a pagar/receber atrasadas ou vencendo em breve
    que ainda não foram avisadas HOJE (uma notificação-resumo por unidade e tipo).
    Só chegam a usuários da unidade com permissão de ver aquele tipo de conta.
    Idempotente. Devolve a quantidade de contas avisadas por tipo."""
    from apps.notificacao.services import enviar_para_usuarios

    hoje = timezone.localdate()
    total = {}
    for cliente in clientes:
        for chave, (modelo, _, permissao, _) in CONFIG.items():
            lista = reverse(f"conta-{chave}-list")
            nome = "a pagar" if chave == "pagar" else "a receber"
            for tipo, consulta, titulo, situacao in (
                (f"{chave}_atrasada", contas_atrasadas(modelo, [cliente], hoje), f"Contas {nome} atrasadas", "atrasadas"),
                (f"{chave}_vencendo", contas_a_vencer(modelo, [cliente], hoje), f"Contas {nome} vencendo", f"vencendo em até {dias_alerta_vencimento()} dia(s)"),
            ):
                contas = list(consulta)
                novas = [c for c in contas if AlertaFinanceiro.objects.get_or_create(cliente=cliente, tipo=tipo, referencia=c.pk, data=hoje)[1]]
                total[tipo] = total.get(tipo, 0) + len(novas)
                usuarios = _destinatarios(cliente, permissao) if novas else []
                if novas and usuarios:
                    try:
                        enviar_para_usuarios(titulo, _resumo(novas, situacao), lista, usuarios)
                    except Exception:  # noqa: BLE001 — alerta é acessório
                        logger.exception("Falha ao enviar alerta financeiro (cliente=%s, tipo=%s)", cliente.pk, tipo)
    return total


def conta_em_atraso_ou_a_vencer(modelo, clientes):
    """Contagens para os avisos das telas: (atrasadas, vencendo em breve)."""
    return contas_atrasadas(modelo, clientes).count(), contas_a_vencer(modelo, clientes).count()
