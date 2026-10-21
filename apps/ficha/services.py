"""Cálculo e gravação de fichas técnicas.

O custo de cada item usa a MESMA regra dos processados
(apps/processado/recalculo.py::custo_do_item): preço pago × usado ÷ comprado,
com conversão de unidades na unidade base e arredondamento só no final.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto
from apps.processado.recalculo import custo_do_item
from web_project.regras import RegraNegocioError

from decimal import ROUND_HALF_UP, Decimal

from .models import Ficha, FichaItem, FichaMaoDeObra

logger = logging.getLogger(__name__)


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


def custo_da_mao_de_obra(colaborador, horas):
    """Valor da hora do colaborador × horas, em centavos, arredondado (meio para cima) só no final."""
    return int((Decimal(colaborador.valor_hora) * Decimal(horas)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def recalcular_ficha(ficha, forcar=False):
    """Recalcula o custo de cada item e de cada linha de mão de obra, e o total da ficha (lê o banco agora)
    e, se o custo mudou (ou `forcar`), refaz as precificações dela."""
    total = 0
    for item in ficha.itens.select_related("insumo", "insumo__medida", "medida"):
        custo = custo_do_item(item.insumo, item.medida, item.quantidade)
        if item.custo != custo:
            item.custo = custo
            item.save(update_fields=["custo"])
        total += custo
    for linha in ficha.mao_de_obra.select_related("colaborador"):
        custo = custo_da_mao_de_obra(linha.colaborador, linha.horas)
        if linha.custo != custo:
            linha.custo = custo
            linha.save(update_fields=["custo"])
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


def recalcular_fichas_do_colaborador(colaborador):
    """Gancho do cadastro de colaboradores: o valor da hora mudou, então refaz as fichas que usam a mão de obra dele (e, por elas, as
    precificações). Inclui fichas inativas. Se uma precificação ficar inviável (margem + taxas >= 100%) o erro é registrado e a
    ficha segue com o custo novo: o cadastro do colaborador nunca é bloqueado por isso; `recalcular_custos` refaz tudo depois."""
    for ficha in Ficha.objects.filter(mao_de_obra__colaborador=colaborador).distinct():
        try:
            recalcular_ficha(ficha)
        except RegraNegocioError:
            logger.warning("Precificação inviável ao recalcular a ficha %s após mudança de colaborador", ficha.pk)


def _texto_mao_de_obra(ficha):
    return "; ".join(f"{m.colaborador.nome}: {m.horas.normalize():f} h" for m in ficha.mao_de_obra.select_related("colaborador")) or None


def _texto_itens(ficha):
    return "; ".join(
        f"{i.insumo.nome}: {i.quantidade.normalize():f} {i.medida.sigla}" for i in ficha.itens.select_related("insumo", "medida")
    ) or None


def salvar_ficha(request, ficha, itens, foto_antes=None, mao_de_obra=None):
    """Grava a ficha e SUBSTITUI os itens por `itens` (FichaItem sem ficha) e, se informada, a mão de obra por `mao_de_obra`
    (FichaMaoDeObra sem ficha; None = não mexe), recalculando custos. Tudo ou nada; levanta ValidationError legível."""
    criando = ficha.pk is None
    try:
        with transaction.atomic():
            antes = None if criando else _texto_itens(ficha)
            antes_mo = None if criando else _texto_mao_de_obra(ficha)
            ficha.save()
            ficha.itens.all().delete()
            for item in itens:
                item.ficha = ficha
            FichaItem.objects.bulk_create(itens)
            if mao_de_obra is not None:
                ficha.mao_de_obra.all().delete()
                for linha in mao_de_obra:
                    linha.ficha = ficha
                FichaMaoDeObra.objects.bulk_create(mao_de_obra)
            recalcular_ficha(ficha, forcar=True)  # a ficha pode ter trocado de produto
            if criando:
                registrar_criacao(request, ficha)
            else:
                alteracoes = comparar(foto_antes, tirar_foto(ficha)) if foto_antes else []
                depois = _texto_itens(ficha)
                if antes != depois:
                    alteracoes.append({"campo": "itens", "rotulo": "Itens da ficha", "antes": antes, "depois": depois})
                if antes_mo != _texto_mao_de_obra(ficha):
                    alteracoes.append({"campo": "mao_de_obra", "rotulo": "Mão de obra da ficha", "antes": antes_mo, "depois": _texto_mao_de_obra(ficha)})
                if alteracoes:
                    registrar_alteracoes(request, ficha, alteracoes)
    except RegraNegocioError as erro:
        raise ValidationError(str(erro)) from None
    return ficha
