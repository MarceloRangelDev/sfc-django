"""
Exportação da listagem de Insumos para Excel (.xlsx) e PDF.

Sempre recebe um queryset JÁ filtrado (cliente, pesquisa, status, ordenação — ver apps/insumo/views.py)
e exporta exatamente esses registros, nunca a tabela inteira: respeita o mesmo isolamento por Cliente e
os mesmos filtros da tela. A montagem do arquivo (Excel e PDF padrão) é a de web_project/exportacao.py.
"""
from django.utils import timezone

from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.regras.dinheiro import DinheiroService

# Sem as colunas Código e Status e sem fotos (decisão do produto): só dados cadastrais e de preço.
CABECALHO = ["Insumo", "Marca", "Unidade", "Quantidade", "Valor pago", "Preço", "Cadastrado em"]


def _linha(insumo):
    quantidade = f"{insumo.quantidade.normalize():f}".replace(".", ",")
    return [
        insumo.nome, insumo.marca or "—", insumo.medida.medida, quantidade, insumo.preco_pago_formatado,
        f"{insumo.preco_unidade_formatado} / {insumo.medida.sigla_preco}" if insumo.preco_unidade is not None else "—",
        timezone.localtime(insumo.criado_em).strftime("%d/%m/%Y %H:%M"),
    ]


def exportar_insumos_excel(queryset):
    # Excel recebe números nas colunas numéricas (Decimal -> float só aqui, na exportação).
    linhas = []
    for insumo in queryset:
        linha = [sanitizar_texto_excel(c) for c in _linha(insumo)]
        linha[3] = float(insumo.quantidade)
        linha[4] = float(DinheiroService.para_decimal(insumo.preco_pago))
        linhas.append(linha)
    return exportar_excel("insumos", "Insumos", CABECALHO, linhas)


def exportar_insumos_pdf(queryset, usuario, filtros=""):
    return exportar_pdf("insumos", "Relatório de Insumos", usuario, CABECALHO, [_linha(i) for i in queryset], filtros)
