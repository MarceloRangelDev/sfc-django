"""PDF do orçamento (reportlab, o mesmo usado nos demais documentos).

Layout: cabeçalho com a logo da EMPRESA (só se houver), nome e dados da empresa (CPF/CNPJ, endereço, e-mail, WhatsApp), título "Orçamento nº X"
e, no canto INFERIOR DIREITO do cabeçalho, a data e hora de emissão; destinatário; tabela de itens com cabeçalho repetido a cada página;
totais; observações; rodapé com "Faturando Certo" e "Página X de Y". Páginas seguintes ganham uma faixa curta com número e data.

Segurança: só entram dados da unidade dona do orçamento (e do usuário que o criou, se for da mesma unidade); o conteúdo é o da `Versao` recebida
(itens omitidos nunca aparecem, nem nos totais); todo texto é escapado antes de ir ao Paragraph.
"""
import logging
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.imagens import servico as imagens
from web_project.exportacao import COR_FAIXA, COR_PRIMARIA, COR_TEXTO_SUAVE, _escapar
from web_project.regras.dinheiro import DinheiroService
from web_project.regras.extenso import formatar_numero_br

logger = logging.getLogger(__name__)
NOME_DO_SISTEMA = "Faturando Certo"
MARGEM = 1.5 * cm


class _CanvasOrcamento(canvas.Canvas):
    """Canvas que conhece o total de páginas (rodapé "Página X de Y") e desenha o rodapé com o nome do sistema."""

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
            largura, _ = self._pagesize
            self.setStrokeColor(colors.HexColor("#d1d5db"))
            self.setLineWidth(0.5)
            self.line(MARGEM, 1.3 * cm, largura - MARGEM, 1.3 * cm)
            self.setFont("Helvetica", 8)
            self.setFillColor(COR_TEXTO_SUAVE)
            self.drawString(MARGEM, 0.85 * cm, NOME_DO_SISTEMA)
            self.drawRightString(largura - MARGEM, 0.85 * cm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def _logo_da_empresa(cliente, lado=2.2 * cm):
    """Imagem da logo da unidade (módulo de imagens, entidade "cliente") ou None. Falha de leitura nunca impede o PDF."""
    try:
        imagem = imagens.ativa("cliente", cliente.pk)
        if imagem is None or imagem.cliente_id != cliente.pk:
            return None
        dados, _ = imagens.variante_bytes(imagem, "lista", "image/jpeg,image/png")
        leitor = ImageReader(BytesIO(dados))
        largura, altura = leitor.getSize()
        escala = min(lado / largura, lado / altura)
        return Image(BytesIO(dados), width=largura * escala, height=altura * escala)
    except Exception:  # noqa: BLE001 (storage fora do ar, imagem corrompida...: o orçamento sai sem logo)
        logger.warning("Logo da empresa indisponível para o PDF do orçamento (cliente %s)", cliente.pk)
        return None


def _dados_do_emitente(orcamento):
    """Linhas de texto da empresa dona do orçamento. Contato: o da empresa; se vazio, o e-mail de quem criou (apenas se for da mesma unidade)."""
    cliente = orcamento.cliente
    rotulo = "CNPJ" if len("".join(c for c in cliente.cpf_cnpj if c.isdigit())) == 14 else "CPF"
    linhas = [f"{rotulo}: {cliente.cpf_cnpj_formatado}"]
    local = ", ".join(p for p in (cliente.endereco, " - ".join(p for p in (cliente.cidade, cliente.uf) if p)) if p)
    if local:
        linhas.append(local)
    email = cliente.email_contato
    autor = orcamento.criado_por
    if not email and autor is not None and getattr(getattr(autor, "profile", None), "cliente_id", None) == cliente.pk:
        email = autor.email
    if email:
        linhas.append(f"E-mail: {email}")
    if cliente.whatsapp:
        linhas.append(f"WhatsApp: {cliente.whatsapp}")
    return linhas


def gerar_pdf(versao, usuario=None):
    """Bytes do PDF da `Versao` (itens já filtrados e totais da própria versão)."""
    orcamento, itens, totais = versao.orcamento, versao.itens, versao.totais
    cliente = orcamento.cliente
    emitido = timezone.localtime()
    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("o", parent=estilos["Normal"], fontName="Helvetica", fontSize=9, leading=12)
    suave = ParagraphStyle("os", parent=normal, textColor=COR_TEXTO_SUAVE, fontSize=8)
    direita = ParagraphStyle("od", parent=normal, alignment=TA_RIGHT)
    titulo = ParagraphStyle("ot", parent=normal, fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_RIGHT, textColor=COR_PRIMARIA)
    nome = ParagraphStyle("on", parent=normal, fontName="Helvetica-Bold", fontSize=12, leading=15)

    buffer = BytesIO()
    documento = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM, topMargin=1.5 * cm, bottomMargin=2 * cm,
                                  title=f"Orçamento nº {orcamento.numero}", author=cliente.nome_razao_social)
    largura_util = A4[0] - 2 * MARGEM
    historia = []

    # --- cabeçalho: logo | empresa | título; data e hora no canto inferior direito ---
    empresa = [Paragraph(_escapar(cliente.nome_fantasia or cliente.nome_razao_social), nome)]
    if cliente.nome_fantasia and cliente.nome_fantasia != cliente.nome_razao_social:
        empresa.append(Paragraph(_escapar(cliente.nome_razao_social), suave))
    empresa += [Paragraph(_escapar(l), normal) for l in _dados_do_emitente(orcamento)]
    logo = _logo_da_empresa(cliente)
    direito = [Paragraph(f"ORÇAMENTO Nº {orcamento.numero}", titulo), Paragraph(_escapar(orcamento.get_status_display()), direita)]
    if orcamento.validade:
        direito.append(Paragraph(f"Válido até {orcamento.validade:%d/%m/%Y}", direita))
    colunas = [logo if logo is not None else "", empresa, direito]
    pesos = [2.6 * cm, largura_util - 2.6 * cm - 6.5 * cm, 6.5 * cm]
    if logo is None:
        pesos = [0.01 * cm, largura_util - 6.5 * cm, 6.5 * cm]
    cabecalho = Table([colunas, ["", "", Paragraph(f"Emitido em {emitido:%d/%m/%Y %H:%M}", ParagraphStyle("oe", parent=direita, fontSize=8, textColor=COR_TEXTO_SUAVE))]],
                      colWidths=pesos)
    cabecalho.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, 0), "TOP"), ("VALIGN", (0, 1), (-1, 1), "BOTTOM"), ("LINEBELOW", (0, 1), (-1, 1), 0.8, COR_PRIMARIA),
        ("LEFTPADDING", (0, 0), (0, -1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 4), ("TOPPADDING", (0, 1), (-1, 1), 4),
    ]))
    historia += [cabecalho, Spacer(1, 0.5 * cm)]

    # --- destinatário ---
    destinatario = [Paragraph("<b>Para:</b> " + _escapar(orcamento.destinatario_nome), normal)]
    contatos = " · ".join(p for p in (orcamento.destinatario_email, orcamento.destinatario_whatsapp) if p)
    if contatos:
        destinatario.append(Paragraph(_escapar(contatos), suave))
    historia += destinatario + [Spacer(1, 0.4 * cm)]

    # --- itens (cabeçalho repetido nas páginas seguintes) ---
    linhas = [["#", "Descrição", "Tipo", "Qtde.", "Preço unit.", "Desconto", "Total"]]
    for numero, item in enumerate(itens, 1):
        linhas.append([
            str(numero), Paragraph(_escapar(item.descricao), normal), item.get_tipo_display(), formatar_numero_br(item.quantidade),
            DinheiroService.formatar(item.preco_unitario), DinheiroService.formatar(item.desconto) if item.desconto else "—", DinheiroService.formatar(item.total),
        ])
    tabela = Table(linhas, repeatRows=1, colWidths=[0.8 * cm, largura_util - 0.8 * cm - 1.9 * cm - 1.8 * cm - 2.6 * cm - 2.3 * cm - 2.6 * cm, 1.9 * cm, 1.8 * cm, 2.6 * cm, 2.3 * cm, 2.6 * cm])
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_FAIXA]), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
    ]
    tabela.setStyle(TableStyle(estilo))
    historia += [tabela, Spacer(1, 0.4 * cm)]

    # --- totais (juntos, nunca quebrados entre páginas) ---
    resumo = [["Soma dos itens", DinheiroService.formatar(totais.subtotal)]]
    if totais.desconto_itens:
        resumo.append(["Descontos nos itens", "- " + DinheiroService.formatar(totais.desconto_itens)])
    if totais.desconto_geral:
        resumo.append(["Desconto geral", "- " + DinheiroService.formatar(totais.desconto_geral)])
    resumo.append(["TOTAL", DinheiroService.formatar(totais.total)])
    quadro = Table(resumo, colWidths=[4.5 * cm, 3.5 * cm], hAlign="RIGHT")
    quadro.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, COR_PRIMARIA), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    bloco_final = [quadro]
    if orcamento.observacoes:
        bloco_final += [Spacer(1, 0.4 * cm), Paragraph("<b>Observações</b>", normal), Paragraph(_escapar(orcamento.observacoes).replace("\n", "<br/>"), normal)]
    historia.append(KeepTogether(bloco_final))

    def faixa_das_paginas_seguintes(c, doc):
        if doc.page > 1:
            c.saveState()
            c.setFont("Helvetica", 8)
            c.setFillColor(COR_TEXTO_SUAVE)
            c.drawString(MARGEM, A4[1] - 1 * cm, f"Orçamento nº {orcamento.numero} - {cliente.nome_fantasia or cliente.nome_razao_social}")
            c.drawRightString(A4[0] - MARGEM, A4[1] - 1 * cm, f"Emitido em {emitido:%d/%m/%Y %H:%M}")
            c.restoreState()

    documento.build(historia, onLaterPages=faixa_das_paginas_seguintes, canvasmaker=_CanvasOrcamento)
    return buffer.getvalue()
