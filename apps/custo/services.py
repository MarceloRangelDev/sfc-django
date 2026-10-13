"""Gravação de custos operacionais e distribuição automática por categoria (em R$)."""
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto
from apps.categoria.models import Categoria
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .calculos import ORIGEM_IGUAL, ORIGEM_MANUAL, custo_por_unidade, distribuir_por_vendas
from .models import CustoAjusteCategoria, CustoOperacional


def salvar_custo(request, custo, foto_antes=None):
    """Grava o custo operacional com auditoria (criação ou alterações campo a campo).
    A distribuição por categoria não é gravada aqui: é calculada a cada consulta
    (`distribuicao_por_categoria`), então mudar um valor já redistribui tudo."""
    criando = custo.pk is None
    with transaction.atomic():
        custo.save()
        if criando:
            registrar_criacao(request, custo)
        else:
            alteracoes = comparar(foto_antes, tirar_foto(custo)) if foto_antes else []
            if alteracoes:
                registrar_alteracoes(request, custo, alteracoes)
    return custo


def total_operacional(cliente):
    """Custo operacional total da unidade: soma dos custos ATIVOS (centavos/mês)."""
    return sum(CustoOperacional.objects.filter(cliente=cliente, ativo=True).values_list("valor", flat=True))


def distribuicao_por_categoria(cliente):
    """Quanto do custo operacional cabe a cada categoria da unidade, em R$ (centavos).

    Devolve (linhas, totais). Cada linha: categoria, valor (centavos do mês), quantidade
    vendida, por_unidade (centavos por item vendido), origem ("vendas", "igual", "manual",
    "zero"), manual (bool) e sem_vendas. Categorias inativas só aparecem se têm ajuste manual.
    `totais`: mensal (custo total), distribuido, nao_distribuido, excedente_manual e
    `sem_historico` (True quando o rateio caiu na divisão igual por falta de vendas).

    A distribuição é CALCULADA aqui, nunca armazenada: só os ajustes manuais ficam no banco
    (CustoAjusteCategoria), o que impede a distribuição de divergir do custo cadastrado."""
    total = total_operacional(cliente)
    ajustes = dict(CustoAjusteCategoria.objects.filter(cliente=cliente).values_list("categoria_id", "valor"))
    categorias = [c for c in Categoria.objects.filter(cliente=cliente).order_by("nome") if c.ativo or c.pk in ajustes]
    distribuicao, nao_distribuido, excedente = distribuir_por_vendas(
        total, [(c.pk, c.quantidade_vendida, c.ativo) for c in categorias], ajustes,
    )
    linhas = []
    for categoria in categorias:
        parte = distribuicao[categoria.pk]
        linhas.append({
            "categoria": categoria, "valor": parte["valor"], "quantidade": categoria.quantidade_vendida,
            "por_unidade": custo_por_unidade(parte["valor"], categoria.quantidade_vendida), "origem": parte["origem"],
            "manual": parte["origem"] == ORIGEM_MANUAL, "sem_vendas": categoria.quantidade_vendida == 0,
        })
    totais = {
        "mensal": total, "distribuido": sum(l["valor"] for l in linhas), "nao_distribuido": nao_distribuido,
        "excedente_manual": excedente, "sem_historico": any(l["origem"] == ORIGEM_IGUAL for l in linhas),
    }
    return linhas, totais


def custo_operacional_da_categoria(categoria):
    """Custo operacional por item vendido da categoria (centavos), a partir da distribuição
    automática/ajustada — é o valor que a Precificação preenche sozinha. Devolve também
    `total` (R$ da categoria no mês), `manual` e um aviso quando não dá para calcular."""
    linhas, _ = distribuicao_por_categoria(categoria.cliente)
    linha = next((l for l in linhas if l["categoria"].pk == categoria.pk), None)
    if linha is None or not linha["valor"]:
        return {"centavos": 0, "total": 0, "manual": bool(linha and linha["manual"]), "aviso": "Nenhum custo operacional distribuído para esta categoria."}
    if not categoria.quantidade_vendida:
        return {
            "centavos": 0, "total": linha["valor"], "manual": linha["manual"],
            "aviso": "Informe a quantidade vendida da categoria para calcular o custo por unidade.",
        }
    return {"centavos": linha["por_unidade"], "total": linha["valor"], "manual": linha["manual"], "aviso": ""}


def _rotulo_valor(valor, manual):
    return f"{DinheiroService.formatar(valor)} ({'ajuste manual' if manual else 'calculado'})"


def ajustar_categoria(request, cliente, categoria, valor):
    """Fixa manualmente o custo operacional da categoria em `valor` (centavos).

    Recusa valor maior que o custo total ou que deixe as categorias com ajuste cobrindo
    um total diferente do cadastrado quando não resta categoria automática para absorver a
    diferença. Auditado (antes/depois). Só a categoria da própria unidade."""
    if categoria.cliente_id != cliente.pk:
        raise RegraNegocioError("Categoria não encontrada.")
    total = total_operacional(cliente)
    if valor < 0:
        raise RegraNegocioError("O valor não pode ser negativo.")
    if valor > total:
        raise RegraNegocioError(f"O valor não pode ser maior que o custo operacional total ({DinheiroService.formatar(total)}).")
    with transaction.atomic():
        linhas, _ = distribuicao_por_categoria(cliente)
        antes = next((l for l in linhas if l["categoria"].pk == categoria.pk), None)
        ajustes = dict(CustoAjusteCategoria.objects.select_for_update().filter(cliente=cliente).values_list("categoria_id", "valor"))
        ajustes[categoria.pk] = valor
        # Simula o resultado: se não sobrar categoria automática para absorver a diferença, a soma
        # dos ajustes precisa fechar exatamente com o total (senão haveria custo sem categoria).
        candidatas = {l["categoria"].pk: (l["quantidade"], l["categoria"].ativo) for l in linhas}
        candidatas[categoria.pk] = (categoria.quantidade_vendida, categoria.ativo)
        _, sobra, excesso = distribuir_por_vendas(total, [(i, q, a) for i, (q, a) in candidatas.items()], ajustes)
        if sobra or excesso:
            raise RegraNegocioError(
                f"Com este valor a soma das categorias ajustadas não fecha com o custo operacional total ({DinheiroService.formatar(total)}). "
                "Deixe ao menos uma categoria no cálculo automático ou ajuste os valores até a soma fechar."
            )
        CustoAjusteCategoria.objects.update_or_create(cliente=cliente, categoria=categoria, defaults={"valor": valor})
        registrar_alteracoes(
            request, categoria,
            [{"campo": "custo_operacional", "rotulo": "Custo operacional da categoria",
              "antes": _rotulo_valor(antes["valor"], antes["manual"]) if antes else None, "depois": _rotulo_valor(valor, True)}],
            modulo="custo",
        )


def restaurar_categoria(request, cliente, categoria):
    """Remove o ajuste manual da categoria: ela volta a seguir o cálculo automático."""
    if categoria.cliente_id != cliente.pk:
        raise RegraNegocioError("Categoria não encontrada.")
    with transaction.atomic():
        ajuste = CustoAjusteCategoria.objects.select_for_update().filter(cliente=cliente, categoria=categoria).first()
        if ajuste is None:
            return False
        antes = ajuste.valor
        ajuste.delete()
        linhas, _ = distribuicao_por_categoria(cliente)
        depois = next((l["valor"] for l in linhas if l["categoria"].pk == categoria.pk), 0)
        registrar_alteracoes(
            request, categoria,
            [{"campo": "custo_operacional", "rotulo": "Custo operacional da categoria",
              "antes": _rotulo_valor(antes, True), "depois": _rotulo_valor(depois, False)}],
            modulo="custo",
        )
    return True


def formatar(centavos):
    return DinheiroService.formatar(centavos)
