"""Exportação de listagens para Excel (.xlsx) e PDF, comum a todos os módulos.

Cada módulo monta o seu cabeçalho e as suas linhas a partir de um queryset JÁ
filtrado (escopo de unidades, pesquisa, status, ordenação — as mesmas regras da
tela) e chama estas funções: o arquivo nunca contém mais do que o usuário está
autorizado a ver, e a camada de formatação (Excel/PDF) fica num lugar só.
"""
from io import BytesIO

import openpyxl
from django.http import HttpResponse
from django.utils import timezone
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sanitizar_texto_excel(valor):
    """Protege contra Formula/CSV Injection: texto que começa com "=", "+",
    "-" ou "@" pode ser lido como fórmula pelo Excel; o apóstrofo neutraliza,
    mantendo o texto visível como está."""
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@") else texto


def exportar_excel(nome_arquivo, titulo_aba, cabecalho, linhas):
    """`linhas`: listas de valores (texto já sanitizado pelo chamador quando
    vier do usuário; números como int/float; None = célula vazia)."""
    workbook = openpyxl.Workbook()
    planilha = workbook.active
    planilha.title = titulo_aba[:31]
    planilha.append(cabecalho)
    for celula in planilha[1]:
        celula.font = openpyxl.styles.Font(bold=True)
    for linha in linhas:
        planilha.append(linha)
    for indice in range(1, len(cabecalho) + 1):
        planilha.column_dimensions[get_column_letter(indice)].width = 22
    resposta = HttpResponse(content_type=TIPO_XLSX)
    resposta["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.xlsx"'
    workbook.save(resposta)
    return resposta


# --- Padrão visual de TODOS os relatórios em PDF (um único lugar) -------------------------------------
COR_PRIMARIA = colors.HexColor("#2f3e54")  # cabeçalho da tabela (azul-marinho do relatório de referência)
COR_FAIXA = colors.HexColor("#f2f2f2")  # linhas alternadas
COR_TEXTO_SUAVE = colors.HexColor("#6b7280")


class _CanvasNumerado(canvas.Canvas):
    """Canvas que sabe o total de páginas: o rodapé mostra "Página X de Y". O PDF é montado inteiro
    em memória antes de ser desenhado, então o total só é conhecido no fim (padrão do reportlab)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas_salvas = []

    def showPage(self):
        self._paginas_salvas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas_salvas)
        for estado in self._paginas_salvas:
            self.__dict__.update(estado)
            self._numero_total = total
            self._desenhar_rodape()
            super().showPage()
        super().save()

    def _desenhar_rodape(self):
        largura, _ = self._pagesize
        margem = 1.5 * cm
        self.setStrokeColor(colors.HexColor("#d1d5db"))
        self.setLineWidth(0.5)
        self.line(margem, 1.3 * cm, largura - margem, 1.3 * cm)
        self.setFont("Helvetica", 8)
        self.setFillColor(COR_TEXTO_SUAVE)
        self.drawString(margem, 0.85 * cm, self._empresa)
        self.drawRightString(largura - margem, 0.85 * cm, f"Página {self._pageNumber} de {self._numero_total}")


def _pesos_das_colunas(cabecalho, linhas):
    """Largura relativa de cada coluna pelo maior texto (limitado), para colunas curtas (datas, valores)
    não dividirem o espaço por igual com colunas de nomes longos."""
    pesos = []
    for indice, titulo in enumerate(cabecalho):
        maior = max([len(str(titulo)) + 3] + [len(str(l[indice])) for l in linhas[:200] if indice < len(l)])
        pesos.append(max(5, min(maior, 40)))  # +3: o título da coluna (em negrito) nunca quebra no meio da palavra
    return pesos


def _alinha_a_direita(valor):
    """Valores monetários, percentuais e quantidades ("R$ 1,00", "12,5%", "+3 kg") ficam à direita."""
    texto = str(valor or "").strip()
    return bool(texto) and (texto[:2] == "R$" or texto[-1:] == "%" or texto[:1] in "+-" and texto[1:2].isdigit())


def exportar_pdf(nome_arquivo, titulo, usuario, cabecalho, linhas, filtros=""):
    """PDF padrão de relatórios tabulares (todos os módulos usam esta função).

    Layout (segue o relatório de referência): nome do sistema centralizado, título do relatório, data/hora
    de emissão e quem emitiu, filtros aplicados (se houver), tabela com cabeçalho repetido em todas as
    páginas e rodapé com a empresa e "Página X de Y". Páginas seguintes ganham só uma linha discreta
    com sistema e título, para o relatório continuar identificável impresso. A lista de colunas é a do chamador; `linhas` com valores
    já em texto (formato brasileiro de moeda e data feito pelo módulo). A empresa do rodapé é a do
    próprio usuário que emitiu; os dados já chegam filtrados pelo escopo dele (tenant) pelo módulo."""
    buffer = BytesIO()
    paisagem = len(cabecalho) > 5
    tamanho = landscape(A4) if paisagem else A4
    margem = 1.5 * cm
    empresa = _nome_da_empresa(usuario)
    emitido = f"Emitido em: {timezone.localtime().strftime('%d/%m/%Y, %H:%M:%S')} — por {usuario.get_username()}"

    def pagina(canvas_, documento, primeira):
        canvas_._empresa = empresa
        if primeira:
            return
        largura, altura = tamanho
        canvas_.saveState()
        canvas_.setFont("Helvetica", 8)
        canvas_.setFillColor(COR_TEXTO_SUAVE)
        canvas_.drawString(margem, altura - 1.0 * cm, "Sistema Faturando Certo")
        canvas_.drawRightString(largura - margem, altura - 1.0 * cm, titulo)
        canvas_.restoreState()

    documento = SimpleDocTemplate(
        buffer, pagesize=tamanho, topMargin=1.6 * cm, bottomMargin=1.8 * cm, leftMargin=margem, rightMargin=margem,
        title=titulo, author="Sistema Faturando Certo",
    )
    estilos = getSampleStyleSheet()
    marca_estilo = ParagraphStyle("marca_relatorio", parent=estilos["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#1f2937"), spaceAfter=2)
    titulo_estilo = ParagraphStyle("titulo_relatorio", parent=estilos["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#1f2937"), spaceAfter=2)
    suave = ParagraphStyle("suave", parent=estilos["Normal"], fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=COR_TEXTO_SUAVE)
    celula = ParagraphStyle("celula", parent=estilos["BodyText"], fontSize=8, leading=10, splitLongWords=True)
    celula_direita = ParagraphStyle("celula_direita", parent=celula, alignment=TA_RIGHT)
    cabecalho_estilo = ParagraphStyle("celula_cabecalho", parent=celula, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)
    cabecalho_direita = cabecalho_estilo  # cabeçalhos sempre centralizados

    elementos = [Paragraph("SISTEMA FATURANDO CERTO", marca_estilo), Paragraph(_escapar(titulo), titulo_estilo), Paragraph(_escapar(emitido), suave)]
    if filtros:
        elementos.append(Paragraph(f"Filtros: {_escapar(filtros)}", suave))
    elementos.append(Spacer(1, 0.4 * cm))

    # Colunas de valores alinhadas à direita: decide pela maioria das linhas da coluna.
    direita = [
        sum(_alinha_a_direita(l[i]) for l in linhas if i < len(l)) > (len(linhas) / 2 if linhas else 1)
        for i in range(len(cabecalho))
    ]
    dados = [[Paragraph(_escapar(c), cabecalho_direita if direita[i] else cabecalho_estilo) for i, c in enumerate(cabecalho)]]
    if linhas:
        for linha in linhas:
            dados.append([Paragraph(_escapar(c), celula_direita if direita[i] else celula) for i, c in enumerate(linha)])
    else:
        dados.append([Paragraph("Nenhum registro encontrado.", celula)] + [""] * (len(cabecalho) - 1))

    pesos = _pesos_das_colunas(cabecalho, linhas)
    util = tamanho[0] - 2 * margem
    larguras = [util * p / sum(pesos) for p in pesos]
    tabela = Table(dados, colWidths=larguras, repeatRows=1)  # repeatRows: o cabeçalho volta em cada página
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_FAIXA]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if not linhas:
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    tabela.setStyle(TableStyle(estilo))
    elementos.append(tabela)

    documento.build(
        elementos, onFirstPage=lambda c, d: pagina(c, d, True), onLaterPages=lambda c, d: pagina(c, d, False), canvasmaker=_CanvasNumerado,
    )
    buffer.seek(0)
    resposta = HttpResponse(buffer, content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.pdf"'
    return resposta


def _nome_da_empresa(usuario):
    """Empresa que aparece no rodapé: a do próprio usuário que emite o relatório."""
    perfil = getattr(usuario, "profile", None)
    cliente = getattr(perfil, "cliente", None)
    return getattr(cliente, "nome_razao_social", None) or "Sistema Faturando Certo"


def _escapar(valor):
    texto = "" if valor is None else str(valor)
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
