"""Cálculo e gravação de fichas técnicas.

O custo de cada item usa a MESMA regra dos processados
(apps/processado/recalculo.py::custo_do_item): preço pago × usado ÷ comprado,
com conversão de unidades na unidade base e arredondamento só no final.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto
from apps.processado.recalculo import custo_do_item
from web_project.regras import RegraNegocioError

from .models import Ficha, FichaItem


def nome_da_ficha(cliente, produto, ignorar_pk=None):
    """Nome interno da ficha, derivado do produto ("Pizza"; se o produto já tem outra ficha
    na unidade, "Pizza (2)", "Pizza (3)"...). A coluna `nome` continua existindo e única por
    unidade para não quebrar fichas antigas, precificações e a API; só deixou de ser digitada."""
    base = produto.nome[:140]
    existentes = set(
        Ficha.objects.filter(cliente=cliente, nome__istartswith=base).exclude(pk=ignorar_pk).values_list("nome", flat=True)
    )
    existentes = {n.lower() for n in existentes}
    candidato, numero = base, 1
    while candidato.lower() in existentes:
        numero += 1
        candidato = f"{base} ({numero})"
    return candidato


def recalcular_ficha(ficha, forcar=False):
    """Recalcula o custo de cada item e o total da ficha (lê os insumos do banco agora)
    e, se o custo mudou (ou `forcar`), refaz as precificações dela."""
    total = 0
    for item in ficha.itens.select_related("insumo", "insumo__medida", "medida"):
        custo = custo_do_item(item.insumo, item.medida, item.quantidade)
        if item.custo != custo:
            item.custo = custo
            item.save(update_fields=["custo"])
        total += custo
    mudou = ficha.custo_total != total
    if mudou:
        ficha.custo_total = total
        ficha.save(update_fields=["custo_total", "atualizado_em"])
    if mudou or forcar:
        from apps.precificacao.services import recalcular_da_ficha  # import tardio: evita ciclo entre apps

        recalcular_da_ficha(ficha)
    return total


def recalcular_fichas_dos_insumos(insumo_ids, cliente_id):
    """Gancho do motor de recálculo (apps/processado/recalculo.py): refaz as
    fichas do cliente que usam os insumos alterados (`None` = todas, usado pelo
    comando `recalcular_custos`). Inclui fichas inativas, para que reativá-las
    não traga custos velhos. Roda na transação do motor."""
    fichas = Ficha.objects.filter(cliente_id=cliente_id)
    if insumo_ids is not None:
        fichas = fichas.filter(itens__insumo_id__in=insumo_ids).distinct()
    for ficha in fichas:
        recalcular_ficha(ficha, forcar=insumo_ids is None)  # recálculo geral também refaz as precificações


def _texto_itens(ficha):
    return "; ".join(
        f"{i.insumo.nome}: {i.quantidade.normalize():f} {i.medida.sigla}" for i in ficha.itens.select_related("insumo", "medida")
    ) or None


def salvar_ficha(request, ficha, itens, foto_antes=None):
    """Grava a ficha e SUBSTITUI os itens por `itens` (FichaItem sem ficha),
    recalculando custos. Tudo ou nada; levanta ValidationError legível."""
    criando = ficha.pk is None
    try:
        with transaction.atomic():
            antes = None if criando else _texto_itens(ficha)
            ficha.save()
            ficha.itens.all().delete()
            for item in itens:
                item.ficha = ficha
            FichaItem.objects.bulk_create(itens)
            recalcular_ficha(ficha, forcar=True)  # a ficha pode ter trocado de produto
            if criando:
                registrar_criacao(request, ficha)
            else:
                alteracoes = comparar(foto_antes, tirar_foto(ficha)) if foto_antes else []
                depois = _texto_itens(ficha)
                if antes != depois:
                    alteracoes.append({"campo": "itens", "rotulo": "Itens da ficha", "antes": antes, "depois": depois})
                if alteracoes:
                    registrar_alteracoes(request, ficha, alteracoes)
    except RegraNegocioError as erro:
        raise ValidationError(str(erro)) from None
    return ficha
