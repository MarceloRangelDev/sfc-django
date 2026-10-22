"""Estoque de PRODUTOS (saldo próprio, sem lote/validade): entrada, baixa manual, ajuste, baixa automática pela venda do PDV e
estorno por cancelamento/devolução.

Separado de `services.py` (estoque de insumos, com lotes e PEPS) porque as regras são mais simples e independentes:
- Só produtos com `Produto.controla_estoque` têm saldo; os demais nunca geram movimento.
- Cada movimento roda numa transação que BLOQUEIA a linha do saldo (select_for_update) e o banco recusa saldo < 0
  (CheckConstraint): a venda do último item simultânea a outra é recusada, nunca deixa o saldo negativo.
- A baixa por venda é idempotente: uma restrição única (um `baixa_venda` por item de venda) impede baixar duas vezes o mesmo item,
  mesmo em reprocessamento ou nova tentativa depois de falha parcial.
- Quem chama já checou permissão e escopo da unidade; o saldo sempre pertence ao cliente dono do produto.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.produto.models import Produto
from web_project.regras import RegraNegocioError

from .models import MOTIVOS, MovimentoProduto, SaldoProduto

ZERO = Decimal("0")
TRES_CASAS = Decimal("0.001")
TIPOS_MANUAIS_DE_SAIDA = (MovimentoProduto.SAIDA, MovimentoProduto.AJUSTE_SAIDA)
TIPOS_MANUAIS_DE_ENTRADA = (MovimentoProduto.ENTRADA, MovimentoProduto.AJUSTE_ENTRADA)


def _nome_do_responsavel(usuario):
    if usuario is None:
        return ""
    return (usuario.get_full_name() or usuario.get_username())[:150]


def anotar_saldo_produto(queryset):
    """Só produtos ATIVOS que controlam estoque, com `saldo` anotado (0 enquanto não há movimento)."""
    return queryset.filter(ativo=True, controla_estoque=True).annotate(
        saldo=Coalesce("saldo_estoque__saldo", Value(ZERO), output_field=DecimalField())
    )


def produtos_com_saldo(clientes):
    """Produtos do estoque (ver `anotar_saldo_produto`) dos clientes informados."""
    return anotar_saldo_produto(Produto.objects.filter(cliente__in=clientes))


def saldo_do_produto(produto):
    saldo = SaldoProduto.objects.filter(produto=produto).values_list("saldo", flat=True).first()
    return saldo if saldo is not None else ZERO


def _quantidade(valor):
    """Quantidade positiva com 3 casas (o PDV vende em até 3 casas); o que arredondar para 0 é recusado."""
    quantidade = Decimal(valor).quantize(TRES_CASAS, rounding=ROUND_HALF_UP)
    if quantidade <= 0:
        raise RegraNegocioError("A quantidade deve ser maior que zero.")
    return quantidade


def _lancar(usuario, produto, tipo, quantidade, motivo="", observacao="", venda_item=None):
    """Aplica o movimento ao saldo (linha bloqueada) e grava o histórico. Chamar DENTRO de transaction.atomic()."""
    SaldoProduto.objects.get_or_create(produto=produto, defaults={"cliente_id": produto.cliente_id})
    saldo = SaldoProduto.objects.select_for_update().get(produto=produto)
    sinal = 1 if tipo in MovimentoProduto.TIPOS_DE_ENTRADA else -1
    novo = saldo.saldo + sinal * quantidade
    if novo < 0:
        raise RegraNegocioError(
            f'Saldo insuficiente de "{produto.nome}": disponível {saldo.saldo.normalize():f}, solicitado {quantidade.normalize():f}.'
        )
    saldo.saldo = novo
    saldo.save(update_fields=["saldo", "atualizado_em"])
    return MovimentoProduto.objects.create(
        cliente_id=produto.cliente_id, produto=produto, tipo=tipo, quantidade=quantidade, saldo_apos=novo, motivo=motivo,
        observacao=(observacao or "")[:255], responsavel=usuario, responsavel_nome=_nome_do_responsavel(usuario),
        venda_item=venda_item, data=timezone.now(),
    )


def _exigir_controle(produto):
    if not produto.controla_estoque:
        raise RegraNegocioError(f'O produto "{produto.nome}" não controla estoque; ative o controle no cadastro do produto.')


# --------------------------------------------------------------------------
# Movimentos manuais
# --------------------------------------------------------------------------

def registrar_entrada_produto(usuario, produto, quantidade, observacao="", tipo=MovimentoProduto.ENTRADA):
    """Entrada (ou ajuste de acréscimo, `tipo=AJUSTE_ENTRADA`) manual. Devolve o movimento."""
    if tipo not in TIPOS_MANUAIS_DE_ENTRADA:
        raise RegraNegocioError("Tipo de entrada inválido.")
    _exigir_controle(produto)
    with transaction.atomic():
        return _lancar(usuario, produto, tipo, _quantidade(quantidade), observacao=observacao)


def registrar_baixa_manual_produto(usuario, produto, quantidade, motivo, observacao="", tipo=MovimentoProduto.SAIDA):
    """Baixa manual (saída ou ajuste de redução). O MOTIVO é obrigatório (lista `MOTIVOS`); "outro" exige a observação.
    Saldo insuficiente é recusado. Devolve o movimento."""
    if tipo not in TIPOS_MANUAIS_DE_SAIDA:
        raise RegraNegocioError("Tipo de baixa inválido.")
    exigir_motivo(motivo, observacao)
    _exigir_controle(produto)
    with transaction.atomic():
        return _lancar(usuario, produto, tipo, _quantidade(quantidade), motivo=motivo, observacao=observacao)


def exigir_motivo(motivo, observacao=""):
    """Regra comum às baixas manuais (insumo e produto): motivo da lista e, em "outro", observação explicando."""
    if motivo not in MOTIVOS:
        raise RegraNegocioError("Informe o motivo da baixa.")
    if motivo == "outro" and not (observacao or "").strip():
        raise RegraNegocioError('No motivo "Outro", descreva na observação.')


# --------------------------------------------------------------------------
# Venda do PDV
# --------------------------------------------------------------------------

def baixar_produto_da_venda(usuario, item):
    """Baixa o saldo do produto da linha de venda, UMA vez por item (idempotente). Sem controle de estoque no produto, não faz nada.
    Saldo insuficiente levanta RegraNegocioError e desfaz a transação inteira da venda (quem chama já está numa transação)."""
    produto = item.produto
    if not produto.controla_estoque:
        return None
    with transaction.atomic():
        # Bloqueia o saldo ANTES de conferir se já foi baixado: duas execuções simultâneas para o mesmo item se serializam aqui.
        SaldoProduto.objects.get_or_create(produto=produto, defaults={"cliente_id": produto.cliente_id})
        SaldoProduto.objects.select_for_update().get(produto=produto)
        if MovimentoProduto.objects.filter(venda_item=item, tipo=MovimentoProduto.BAIXA_VENDA).exists():
            return None
        numero = item.venda.numero
        try:
            return _lancar(usuario, produto, MovimentoProduto.BAIXA_VENDA, _quantidade(item.quantidade), observacao=f"Venda PDV #{numero}", venda_item=item)
        except RegraNegocioError as erro:
            raise RegraNegocioError(f"{item.descricao}: {erro}") from None


def estornar_produto_da_venda(usuario, item, fracao):
    """Devolve ao saldo `fracao` (0..1) da quantidade baixada do item, sem passar do que foi baixado (devoluções parciais somam).
    Só há o que estornar se o item foi baixado antes; devolve se estornou algo."""
    baixado = MovimentoProduto.objects.filter(venda_item=item, tipo=MovimentoProduto.BAIXA_VENDA).aggregate(
        total=Coalesce(Sum("quantidade"), Value(ZERO), output_field=DecimalField())
    )["total"]
    if baixado <= 0:
        return False
    with transaction.atomic():
        SaldoProduto.objects.select_for_update().filter(produto=item.produto).first()  # serializa estornos concorrentes do mesmo produto
        estornado = MovimentoProduto.objects.filter(venda_item=item, tipo=MovimentoProduto.ESTORNO_VENDA).aggregate(
            total=Coalesce(Sum("quantidade"), Value(ZERO), output_field=DecimalField())
        )["total"]
        quantidade = min((baixado * fracao).quantize(TRES_CASAS, rounding=ROUND_HALF_UP), baixado - estornado)
        if quantidade <= 0:
            return False
        _lancar(usuario, item.produto, MovimentoProduto.ESTORNO_VENDA, quantidade,
                observacao=f"Devolução/cancelamento da venda PDV #{item.venda.numero}", venda_item=item)
    return True


# --------------------------------------------------------------------------
# Consistência
# --------------------------------------------------------------------------

def verificar_consistencia_produtos(clientes=None, corrigir=False):
    """Compara cada saldo com a soma do histórico. Devolve [(saldo, gravado, pelo histórico)]; com `corrigir` o histórico manda."""
    saldos = SaldoProduto.objects.select_related("produto")
    if clientes is not None:
        saldos = saldos.filter(cliente__in=clientes)
    divergencias = []
    for linha in saldos:
        pelo_historico = ZERO
        for tipo, quantidade in MovimentoProduto.objects.filter(produto=linha.produto).values_list("tipo", "quantidade"):
            pelo_historico += quantidade if tipo in MovimentoProduto.TIPOS_DE_ENTRADA else -quantidade
        if pelo_historico != linha.saldo:
            divergencias.append((linha, linha.saldo, pelo_historico))
            if corrigir:
                SaldoProduto.objects.filter(pk=linha.pk).update(saldo=pelo_historico)
    return divergencias
