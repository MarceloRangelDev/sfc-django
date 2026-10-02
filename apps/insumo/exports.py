"""
Exportação da listagem de Insumos para Excel (.xlsx) e PDF.

Sempre recebe um queryset JÁ filtrado (cliente, pesquisa, ordenação — ver
apps/insumo/views.py) e exporta exatamente esses registros, nunca a tabela
inteira: a exportação respeita o mesmo isolamento por Cliente e os mesmos
filtros que a tela está mostrando.
"""
from io import BytesIO

import openpyxl
from django.http import HttpResponse
from django.utils import timezone
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from web_project.regras.dinheiro import DinheiroService

CABECALHO = [
    "Código",
    "Insumo",
    "Unidade",
    "Quantidade",
    "Valor pago (R$)",
    "Preço por KG/L/UN (R$)",
    "Status",
    "Cadastrado em",
]


def _sanitizar_texto_excel(valor):
    """Protege contra Formula/CSV Injection: se um texto começar com
    "=", "+", "-" ou "@", o Excel pode interpretá-lo como fórmula ao abrir
    o arquivo. Prefixar com aspas simples neutraliza isso, mantendo o
    texto visível como está."""
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@"):
        return "'" + texto
    return texto


def _linha_insumo(insumo):
    return [
        insumo.codigo,
        _sanitizar_texto_excel(insumo.nome),
        insumo.medida.medida,
        # Excel recebe número (Decimal -> float só aqui, na camada de
        # exportação; o banco e os cálculos continuam em centavos/Decimal).
        float(insumo.quantidade),
        float(DinheiroService.para_decimal(insumo.preco_pago)),
        float(DinheiroService.para_decimal(insumo.preco_unidade)) if insumo.preco_unidade is not None else None,
        "Ativo" if insumo.ativo else "Inativo",
        timezone.localtime(insumo.criado_em).strftime("%d/%m/%Y %H:%M"),
    ]


def exportar_insumos_excel(queryset):
    workbook = openpyxl.Workbook()
    planilha = workbook.active
    planilha.title = "Insumos"

    planilha.append(CABECALHO)
    for celula in planilha[1]:
        celula.font = openpyxl.styles.Font(bold=True)

    for insumo in queryset:
        planilha.append(_linha_insumo(insumo))

    for indice in range(1, len(CABECALHO) + 1):
        planilha.column_dimensions[get_column_letter(indice)].width = 22

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="insumos.xlsx"'
    workbook.save(response)
    return response


def exportar_insumos_pdf(queryset, usuario, termo_pesquisa=""):
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
    )
    estilos = getSampleStyleSheet()
    elementos = [
        Paragraph("Sistema Faturando Certo", estilos["Title"]),
        Paragraph("Relatório de Insumos", estilos["Heading2"]),
        Paragraph(
            f"Gerado em {timezone.localtime().strftime('%d/%m/%Y %H:%M')} por {usuario.username}",
            estilos["Normal"],
        ),
    ]
    if termo_pesquisa:
        elementos.append(Paragraph(f"Filtro de pesquisa: \"{termo_pesquisa}\"", estilos["Normal"]))
    elementos.append(Spacer(1, 0.5 * cm))

    dados = [["Código", "Insumo", "Unidade", "Quantidade", "Valor pago", "Preço por KG/L/UN", "Status"]]
    for insumo in queryset:
        quantidade_texto = f"{insumo.quantidade:.4f}".rstrip("0").rstrip(".")
        dados.append(
            [
                insumo.codigo,
                insumo.nome,
                insumo.medida.medida,
                quantidade_texto,
                insumo.preco_pago_formatado,
                f"{insumo.preco_unidade_formatado} / {insumo.medida.sigla_preco}",
                "Ativo" if insumo.ativo else "Inativo",
            ]
        )

    tabela = Table(dados, repeatRows=1)
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#696cff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f9")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elementos.append(tabela)

    documento.build(elementos)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="insumos.pdf"'
    return response
