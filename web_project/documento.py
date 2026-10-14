"""Documento individual (PDF ou Excel) de um Processado ou de uma Ficha técnica.

Usado pela tela e pela API para que as duas entreguem exatamente o mesmo conteúdo.

Regras de segurança:
- o registro vem sempre do escopo do usuário (tenant/unidades) — quem chama passa o objeto já
  filtrado; aqui nada é buscado por id vindo do cliente;
- receita e observações só entram se o usuário tem `<app>.ver_receita` E pediu `incluir_receita`.
  Sem a permissão o pedido é ignorado (não é erro: o arquivo simplesmente sai sem esses textos);
- todo texto é escapado (PDF) ou neutralizado contra fórmulas (Excel);
- a exportação é auditada.
"""
from io import BytesIO

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.auditoria.utils import registrar as registrar_auditoria
from web_project.acesso import clientes_dos_itens
from web_project.exportacao import (
    COR_FAIXA,
    COR_PRIMARIA,
    COR_TEXTO_SUAVE,
    _CanvasNumerado,
    _escapar,
    _nome_da_empresa,
    exportar_excel,
    sanitizar_texto_excel,
)
from web_project.regras.extenso import formatar_numero_br

CABECALHO_ITENS = ["Insumo", "Quantidade", "Unidade", "Custo"]


def usuario_pode_ver_receita(usuario, objeto):
    return usuario.has_perm(f"{objeto._meta.app_label}.ver_receita")


def deve_incluir_receita(usuario, objeto, pedido):
    """True só se o usuário pediu E tem permissão (o pedido sem permissão é ignorado)."""
    return bool(pedido) and usuario_pode_ver_receita(usuario, objeto)


def interpretar_booleano(valor):
    return str(valor or "").strip().lower() in ("1", "true", "sim", "on", "yes")


def montar_documento(objeto, incluir_receita):
    """Dados neutros do documento: título, resumo (rótulo/valor), itens e textos opcionais."""
    itens = list(objeto.itens.select_related("insumo", "medida").order_by("id"))
    linhas = [[i.insumo.nome, formatar_numero_br(i.quantidade), i.medida.sigla, i.custo_formatado] for i in itens]
    resumo = [("Unidade", objeto.cliente.nome_razao_social), ("Custo total", objeto.custo_total_formatado),
              ("Status", "Ativo" if objeto.ativo else "Inativo")]
    if objeto._meta.model_name == "processado":
        titulo = f"Processado: {objeto.nome}"
        resumo.insert(1, ("Rendimento", f"{formatar_numero_br(objeto.rendimento_quantidade)} {objeto.rendimento_medida.sigla}"))
        resumo.insert(3, ("Custo por KG/L", f"{objeto.custo_unitario_formatado} / {objeto.rendimento_medida.sigla_preco}"))
    else:
        titulo = f"Ficha técnica: {objeto.nome}"
        resumo.insert(1, ("Produto", objeto.produto.nome))
    textos = []
    if incluir_receita:
        if objeto.receita:
            textos.append(("Receita / modo de preparo", objeto.receita))
        if objeto.observacoes:
            textos.append(("Observações", objeto.observacoes))
    return {"titulo": titulo, "resumo": resumo, "cabecalho": CABECALHO_ITENS, "linhas": linhas, "textos": textos}


def _arquivo_seguro(objeto):
    return f"{objeto._meta.model_name}-{objeto.pk}"


def exportar_documento(request, objeto, formato, incluir_receita_pedido):
    """Gera o arquivo, audita e devolve o HttpResponse. `formato`: 'pdf' ou 'xlsx'."""
    if formato not in ("pdf", "xlsx"):
        raise Http404("Formato inválido.")
    incluir = deve_incluir_receita(request.user, objeto, incluir_receita_pedido)
    dados = montar_documento(objeto, incluir)
    registrar_auditoria(
        request, acao="exportar", modulo=objeto._meta.app_label, tabela=objeto._meta.db_table,
        registro_id=str(objeto.pk), registro_nome=str(objeto),
        descricao=f"Exportação de {objeto._meta.verbose_name.lower()} em {formato}"
                  f"{' com receita/observações' if dados['textos'] else ''}.",
        silencioso=False,
    )
    if formato == "xlsx":
        return _excel(objeto, dados)
    return _pdf(request.user, objeto, dados)


def _excel(objeto, dados):
    linhas = [[sanitizar_texto_excel(c) for c in l] for l in dados["linhas"]]
    linhas.append([])
    linhas += [[sanitizar_texto_excel(r), sanitizar_texto_excel(v)] for r, v in dados["resumo"]]
    for titulo, texto in dados["textos"]:
        linhas += [[], [sanitizar_texto_excel(titulo)]]
        linhas += [[sanitizar_texto_excel(l)] for l in texto.split("\n")]
    # nome da aba: o Excel proíbe ":" e outros símbolos em títulos de aba, então não usa o título do documento
    return exportar_excel(_arquivo_seguro(objeto), objeto._meta.verbose_name, dados["cabecalho"], linhas)


def _paragrafo_texto(texto, estilo):
    """Texto simples -> parágrafos: quebras de linha viram <br/>; o conteúdo é escapado (sem HTML)."""
    return Paragraph("<br/>".join(_escapar(l) for l in texto.split("\n")), estilo)


def _pdf(usuario, objeto, dados):
    buffer = BytesIO()
    margem = 1.5 * cm
    empresa = _nome_da_empresa(usuario)
    emitido = f"Emitido em: {timezone.localtime().strftime('%d/%m/%Y, %H:%M:%S')} — por {usuario.get_username()}"
    documento = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.6 * cm, bottomMargin=1.8 * cm, leftMargin=margem,
                                  rightMargin=margem, title=dados["titulo"], author="Sistema Faturando Certo")
    estilos = getSampleStyleSheet()
    escuro = colors.HexColor("#1f2937")
    marca = ParagraphStyle("doc_marca", parent=estilos["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_CENTER, textColor=escuro, spaceAfter=2)
    titulo = ParagraphStyle("doc_titulo", parent=estilos["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, alignment=TA_CENTER, textColor=escuro, spaceAfter=2)
    suave = ParagraphStyle("doc_suave", parent=estilos["Normal"], fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=COR_TEXTO_SUAVE)
    celula = ParagraphStyle("doc_celula", parent=estilos["BodyText"], fontSize=9, leading=11, splitLongWords=True)
    direita = ParagraphStyle("doc_direita", parent=celula, alignment=TA_RIGHT)
    cab = ParagraphStyle("doc_cab", parent=celula, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)
    secao = ParagraphStyle("doc_secao", parent=estilos["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, textColor=escuro, spaceBefore=10, spaceAfter=4)

    elementos = [Paragraph("SISTEMA FATURANDO CERTO", marca), Paragraph(_escapar(dados["titulo"]), titulo),
                 Paragraph(_escapar(emitido), suave), Spacer(1, 0.4 * cm)]

    resumo = Table([[Paragraph(f"<b>{_escapar(r)}</b>", celula), Paragraph(_escapar(v), celula)] for r, v in dados["resumo"]],
                   colWidths=[4 * cm, 14 * cm])
    resumo.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde3")),
                                ("BACKGROUND", (0, 0), (0, -1), COR_FAIXA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elementos += [resumo, Paragraph("Ficha técnica (itens)", secao)]

    linhas = [[Paragraph(_escapar(c), cab) for c in dados["cabecalho"]]]
    for linha in dados["linhas"]:
        linhas.append([Paragraph(_escapar(c), celula if i < 3 and i != 1 else direita) for i, c in enumerate(linha)])
    tabela = Table(linhas, colWidths=[8 * cm, 3.5 * cm, 2.5 * cm, 4 * cm], repeatRows=1)
    tabela.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
                                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_FAIXA]),
                                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde3")),
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elementos.append(tabela)

    for nome, texto in dados["textos"]:
        elementos += [Paragraph(_escapar(nome), secao), _paragrafo_texto(texto, celula)]

    def pagina(canvas_, _documento):
        canvas_._empresa = empresa

    documento.build(elementos, onFirstPage=pagina, onLaterPages=pagina, canvasmaker=_CanvasNumerado)
    buffer.seek(0)
    resposta = HttpResponse(buffer, content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="{_arquivo_seguro(objeto)}.pdf"'
    return resposta


class DocumentoView(View):
    """GET ?formato=pdf|xlsx[&incluir_receita=1] -> arquivo do registro (Processado ou Ficha).
    O registro é buscado só no escopo de visualização do usuário (outro tenant = 404)."""

    modelo = None

    def get(self, request, pk):
        objeto = get_object_or_404(self.modelo.objects.select_related("cliente"), pk=pk,
                                   cliente__in=clientes_dos_itens(request.user, "view"))
        return exportar_documento(request, objeto, request.GET.get("formato", "pdf"),
                                  interpretar_booleano(request.GET.get("incluir_receita")))
