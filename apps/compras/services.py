"""Lista de compras: sugestões a partir do estoque e inclusão idempotente na lista.

CÁLCULO DA QUANTIDADE SUGERIDA (por insumo ATIVO da unidade, com mínimo definido e saldo abaixo dele):

    bruto     = mínimo × fator_de_reposição − saldo           (fator padrão 2: repor até o dobro do mínimo)
    sugerido  = bruto arredondado PARA CIMA ao múltiplo de compra, se houver um (embalagem); senão = bruto

Tudo na unidade do insumo. Ex.: mínimo 5 kg, fator 2, saldo 3 kg, múltiplo 5 kg → bruto 7 → sugerido 10 kg.
Insumo já presente na lista não é sugerido de novo. O servidor SEMPRE recalcula: o cliente só diz QUAIS
insumos adicionar, nunca a quantidade.
"""
import math
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.auditoria.utils import registrar_criacao
from apps.estoque.services import insumos_com_saldo

from .models import ItemCompra

QUATRO_CASAS = Decimal("0.0001")


def quantidade_sugerida(minimo, saldo, fator, multiplo=None):
    """Regra de cálculo (função pura). Devolve Decimal com 4 casas; 0 se não precisa comprar."""
    bruto = minimo * fator - saldo
    if bruto <= 0:
        return Decimal("0")
    if multiplo:
        bruto = Decimal(math.ceil(bruto / multiplo)) * multiplo
    return bruto.quantize(QUATRO_CASAS)


def _fmt(valor):
    return f"{Decimal(valor).normalize():f}"


def sugestoes_de_compra(cliente):
    """Sugestões da unidade `cliente`: lista de dicts (insumo, saldo, minimo, fator, multiplo, quantidade, calculo)."""
    ja_na_lista = set(ItemCompra.objects.filter(cliente=cliente, insumo__isnull=False).values_list("insumo_id", flat=True))
    candidatos = (
        insumos_com_saldo([cliente]).filter(minimo__gt=0, saldo__lt=F("minimo"))
        .exclude(pk__in=ja_na_lista).select_related("medida", "estoque_minimo").order_by("nome")
    )
    resultado = []
    for insumo in candidatos:
        config = insumo.estoque_minimo
        quantidade = quantidade_sugerida(insumo.minimo, insumo.saldo, config.fator_reposicao, config.multiplo_compra)
        if quantidade <= 0:
            continue
        calculo = f"({_fmt(insumo.minimo)} × {_fmt(config.fator_reposicao)}) − {_fmt(insumo.saldo)}"
        if config.multiplo_compra:
            calculo += f", arredondado ao múltiplo de {_fmt(config.multiplo_compra)}"
        resultado.append({
            "insumo": insumo, "saldo": insumo.saldo, "minimo": insumo.minimo, "fator": config.fator_reposicao,
            "multiplo": config.multiplo_compra, "quantidade": quantidade, "calculo": f"{calculo} = {_fmt(quantidade)} {insumo.medida.sigla}",
        })
    return resultado


def adicionar_sugestoes(request, cliente, insumo_ids=None):
    """Inclui na lista as sugestões atuais (todas, ou só as de `insumo_ids`). IDEMPOTENTE: repetir a chamada
    (reenvio do app/duplo clique) não duplica nada; quem já está na lista é ignorado. Cada item criado é auditado
    na mesma transação. Devolve os itens criados."""
    ids = None if insumo_ids is None else {int(i) for i in insumo_ids}
    criados = []
    for sugestao in sugestoes_de_compra(cliente):
        insumo = sugestao["insumo"]
        if ids is not None and insumo.pk not in ids:
            continue
        try:
            with transaction.atomic():
                item = ItemCompra.objects.create(
                    cliente=cliente, insumo=insumo, descricao=insumo.nome, quantidade=sugestao["quantidade"], medida=insumo.medida,
                    origem=ItemCompra.ORIGEM_SUGESTAO, criado_por=request.user if request.user.is_authenticated else None,
                    observacao=f"Sugestão: {sugestao['calculo']}"[:255],
                )
                registrar_criacao(request, item)
        except IntegrityError:  # outra requisição incluiu o mesmo insumo agora: ignora
            continue
        criados.append(item)
    return criados
