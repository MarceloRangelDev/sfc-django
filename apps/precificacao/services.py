"""Gravação, recálculo e prévia de precificações.

Quem calcula é sempre `calculos.calcular_preco`; aqui só se monta a entrada
(custo da ficha, taxas do canal) e se grava o resultado.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto
from web_project.regras import RegraNegocioError

from .calculos import calcular_preco
from .models import Precificacao

CAMPOS_CALCULADOS = [
    "nome", "produto", "canal_nome", "canal_descricao", "taxa_percentual", "taxa_fixa", "custo_operacional", "custo_ficha",
    "custo_total", "taxas_percentuais_valor", "lucro", "preco_venda", "cmv_percentual", "atualizado_em",
]


def custo_operacional_automatico(ficha):
    """Custo operacional por unidade que a distribuição de custos atribui à categoria do produto da ficha."""
    from apps.custo.services import custo_operacional_da_categoria  # import tardio: evita ciclo entre apps

    return custo_operacional_da_categoria(ficha.produto.categoria)["centavos"]


def preencher(precificacao):
    """Resolve produto e canal e calcula o preço, preenchendo os campos
    calculados (sem gravar). Canal cadastrado: nome/descrição/taxas são cópia
    dele (a edição do canal repropaga); sem canal: valem os digitados.
    Levanta RegraNegocioError se a fórmula não tem resultado."""
    p = precificacao
    p.produto = p.ficha.produto
    if p.canal_id:
        p.canal_nome, p.canal_descricao = p.canal.nome, p.canal.descricao
        p.taxa_percentual, p.taxa_fixa = p.canal.taxa_percentual_total, p.canal.taxa_fixa_total
    p.nome = (f"{p.ficha.nome} — {p.nome_do_canal}" if p.nome_do_canal else p.ficha.nome)[:150]
    resultado = calcular_preco(
        p.ficha.custo_total, p.custo_operacional, p.taxa_percentual, p.taxa_fixa,
        p.metodo_lucro, p.percentual_lucro, p.valor_lucro,
    )
    p.custo_ficha, p.custo_total = resultado.custo_ficha, resultado.custo_total
    p.taxas_percentuais_valor, p.lucro = resultado.taxas_percentuais_valor, resultado.lucro
    p.preco_venda, p.cmv_percentual = resultado.preco_venda, resultado.cmv_percentual
    return resultado


def salvar_precificacao(request, precificacao, foto_antes=None):
    """Calcula e grava (com auditoria) numa transação; ValidationError legível
    se o cálculo não for possível (ex.: margem + taxas ≥ 100 %)."""
    criando = precificacao.pk is None
    try:
        with transaction.atomic():
            preencher(precificacao)
            if precificacao.usar_no_pdv and precificacao.ativo:  # ANTES de salvar (a restrição do banco permite uma só por produto): as outras perdem a marca (auditado)
                for outra in Precificacao.objects.filter(produto_id=precificacao.produto_id, usar_no_pdv=True).exclude(pk=precificacao.pk):
                    Precificacao.objects.filter(pk=outra.pk).update(usar_no_pdv=False)
                    registrar_alteracoes(request, outra, [{"campo": "usar_no_pdv", "rotulo": "Usar no PDV", "antes": "Sim", "depois": "Não"}])
            precificacao.save()
            if criando:
                registrar_criacao(request, precificacao)
            else:
                alteracoes = comparar(foto_antes, tirar_foto(precificacao)) if foto_antes else []
                if alteracoes:
                    registrar_alteracoes(request, precificacao, alteracoes)
    except RegraNegocioError as erro:
        raise ValidationError(str(erro)) from None
    return precificacao


def _recalcular(consulta, so_automaticas=False):
    """Refaz as precificações da consulta. As que NÃO foram ajustadas à mão (`custo_operacional_manual`
    falso) também têm o custo operacional renovado pela distribuição vigente; as ajustadas mantêm o
    valor do usuário. `so_automaticas`: ignora falha de cálculo de uma linha (custo cadastrado não
    pode ser barrado por uma precificação com margem + taxas ≥ 100 %)."""
    from apps.custo.services import distribuicao_por_categoria  # import tardio: evita ciclo entre apps

    distribuicoes = {}  # cliente_id -> {categoria_id: custo por unidade}; uma consulta por unidade
    for precificacao in consulta.select_related("ficha", "ficha__produto", "canal"):
        if not precificacao.custo_operacional_manual:
            if precificacao.cliente_id not in distribuicoes:
                linhas, _ = distribuicao_por_categoria(precificacao.cliente)
                distribuicoes[precificacao.cliente_id] = {l["categoria"].pk: l["por_unidade"] for l in linhas}
            precificacao.custo_operacional = distribuicoes[precificacao.cliente_id].get(precificacao.ficha.produto.categoria_id, 0)
        try:
            preencher(precificacao)
        except RegraNegocioError:
            if so_automaticas:
                continue
            raise
        precificacao.save(update_fields=CAMPOS_CALCULADOS)


def recalcular_custo_operacional_automatico(cliente_id):
    """Chamado quando custos, ajustes ou vendas por categoria mudam: refaz as precificações da unidade
    que seguem a distribuição automática (as ajustadas manualmente ficam como o usuário deixou)."""
    _recalcular(Precificacao.objects.filter(cliente_id=cliente_id, custo_operacional_manual=False), so_automaticas=True)


def recalcular_da_ficha(ficha):
    """Refaz as precificações da ficha (custo mudou ou a ficha trocou de
    produto). Chamado por apps/ficha/services.py::recalcular_ficha, dentro da
    transação do recálculo. Valores derivados: não geram linha de auditoria."""
    _recalcular(Precificacao.objects.filter(ficha=ficha))


def recalcular_do_canal(canal):
    """Refaz as precificações que usam o canal (taxas mudaram). Chamado por
    apps/canal/services.py::salvar_canal; se alguma ficar sem preço possível,
    levanta ValidationError e a edição do canal é desfeita."""
    try:
        _recalcular(Precificacao.objects.filter(canal=canal))
    except RegraNegocioError:
        raise ValidationError(
            "As novas taxas inviabilizam precificações que usam este canal (margem + taxas chegariam a 100%). "
            "Ajuste as precificações ou reduza as taxas."
        ) from None


def itens_da_ficha(ficha):
    """Itens da ficha com custo, para exibir na precificação."""
    return list(ficha.itens.select_related("insumo", "medida"))
