"""Etiquetas de insumo em PDF: nome, lote (opcional), emissão, validade e QR Code.

Formatos (escolha do usuário): "a4" (grade 3x8 de 63,5x33,9 mm, padrão de folha adesiva) e "termica"
(uma etiqueta 50x30 mm por página, para impressora térmica). O QR usa o QR nativo do reportlab (sem dependência nova).

CONTEÚDO DO QR: só a URL `<BASE_URL>/i/<uuid do insumo>`. O UUID é aleatório, estável e não revela dados; a
página de destino exige login e o tenant correto (apps/insumo/views.py::InsumoPorUuidView), então escanear a etiqueta
fora da empresa não mostra nada. Nenhum preço, lote ou dado pessoal vai dentro do código.

VALIDADE: o usuário escolhe, a cada geração, entre a data DIGITADA e a CALCULADA (emissão + `dias_validade` do insumo).
"""
from datetime import timedelta
from io import BytesIO

from django import forms
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from apps.auditoria.utils import registrar as registrar_auditoria

FORMATO_A4, FORMATO_TERMICA = "a4", "termica"
FORMATOS = [(FORMATO_A4, "Folha A4 (3 × 8 etiquetas)"), (FORMATO_TERMICA, "Térmica 50 × 30 mm")]
MODO_DIGITADA, MODO_CALCULADA = "digitada", "calculada"
MODOS = [(MODO_DIGITADA, "Data de validade digitada"), (MODO_CALCULADA, "Calculada (emissão + dias de validade do insumo)")]
MAX_COPIAS = 200

A4_COLUNAS, A4_LINHAS = 3, 8
A4_LARGURA, A4_ALTURA = 63.5 * mm, 33.9 * mm
A4_MARGEM_ESQ, A4_MARGEM_TOPO = 7.2 * mm, 13.0 * mm
TERMICA = (50 * mm, 30 * mm)


class EtiquetaParametrosForm(forms.Form):
    """Parâmetros da etiqueta de UM insumo (já definido). Usado pela tela e pela API."""

    lote = forms.CharField(max_length=60, required=False, label="Lote (opcional)", widget=forms.TextInput(attrs={"class": "form-control"}))
    emissao = forms.DateField(required=False, label="Data de emissão", widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"))
    modo_validade = forms.ChoiceField(choices=MODOS, initial=MODO_DIGITADA, label="Validade", widget=forms.RadioSelect)
    validade = forms.DateField(required=False, label="Data de validade", widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"))
    copias = forms.IntegerField(min_value=1, max_value=MAX_COPIAS, initial=1, label="Quantidade de etiquetas", widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": MAX_COPIAS}))
    formato = forms.ChoiceField(choices=FORMATOS, initial=FORMATO_A4, label="Formato", widget=forms.Select(attrs={"class": "form-select"}))

    def __init__(self, *args, insumo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.insumo = insumo

    def clean(self):
        dados = super().clean()
        emissao = dados.get("emissao") or timezone.localdate()
        dados["emissao"] = emissao
        modo = dados.get("modo_validade")
        if modo == MODO_CALCULADA:
            dias = getattr(self.insumo, "dias_validade", None)
            if not dias:
                self.add_error("modo_validade", "Este insumo não tem validade padrão (dias). Cadastre-a no insumo ou digite a data.")
            else:
                dados["validade"] = emissao + timedelta(days=dias)
        elif modo == MODO_DIGITADA:
            if not dados.get("validade"):
                self.add_error("validade", "Informe a data de validade ou escolha a validade calculada.")
            elif dados["validade"] < emissao:
                self.add_error("validade", "A validade não pode ser anterior à emissão.")
        dados["lote"] = " ".join((dados.get("lote") or "").split())
        return dados


def url_do_qr(request, insumo):
    """URL estável do QR: BASE_URL configurada (preferida, pois o QR é impresso) ou o host da requisição."""
    base = (getattr(settings, "BASE_URL", "") or "").rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    return f"{base}/i/{insumo.uuid}"


def _data(d):
    return d.strftime("%d/%m/%Y")


def _desenhar_qr(c, url, x, y, lado):
    qr = QrCodeWidget(url)
    x0, y0, x1, y1 = qr.getBounds()
    desenho = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    desenho.add(qr)
    renderPDF.draw(desenho, c, x, y)


def _desenhar_etiqueta(c, x, y, largura, altura, dados):
    """Uma etiqueta no retângulo (x, y) = canto inferior esquerdo. Texto à esquerda, QR à direita."""
    pad = 1.8 * mm
    lado = min(altura - 2 * pad, 22 * mm)
    _desenhar_qr(c, dados["url"], x + largura - pad - lado, y + (altura - lado) / 2, lado)
    largura_texto = largura - lado - 3 * pad
    tamanho_nome = 8 if altura > 32 * mm else 7
    todas = simpleSplit(dados["nome"], "Helvetica-Bold", tamanho_nome, largura_texto)
    linhas = todas[:3]
    if len(todas) > 3:  # nome longo: marca o corte em vez de truncar em silêncio
        linhas[-1] = linhas[-1][:-1].rstrip() + "…"
    topo = y + altura - pad - tamanho_nome
    c.setFont("Helvetica-Bold", tamanho_nome)
    for linha in linhas:
        c.drawString(x + pad, topo, linha)
        topo -= tamanho_nome + 1
    c.setFont("Helvetica", 6.5)
    baixo = y + pad + 2
    for texto, negrito in reversed([(f"Val.: {_data(dados['validade'])}", True), (f"Emissão: {_data(dados['emissao'])}", False)] + ([(f"Lote: {dados['lote'][:24]}", False)] if dados["lote"] else [])):
        c.setFont("Helvetica-Bold" if negrito else "Helvetica", 7 if negrito else 6.5)
        c.drawString(x + pad, baixo, texto)
        baixo += 8


def gerar_pdf(dados, copias, formato):
    """Bytes do PDF com `copias` etiquetas iguais. `dados`: nome, lote, emissao, validade, url."""
    buffer = BytesIO()
    if formato == FORMATO_TERMICA:
        c = canvas.Canvas(buffer, pagesize=TERMICA)
        for _ in range(copias):
            _desenhar_etiqueta(c, 0, 0, TERMICA[0], TERMICA[1], dados)
            c.showPage()
    else:
        c = canvas.Canvas(buffer, pagesize=A4)
        por_pagina = A4_COLUNAS * A4_LINHAS
        for numero in range(copias):
            posicao = numero % por_pagina
            coluna, linha = posicao % A4_COLUNAS, posicao // A4_COLUNAS
            x = A4_MARGEM_ESQ + coluna * A4_LARGURA
            y = A4[1] - A4_MARGEM_TOPO - (linha + 1) * A4_ALTURA
            _desenhar_etiqueta(c, x, y, A4_LARGURA, A4_ALTURA, dados)
            if posicao == por_pagina - 1 and numero != copias - 1:
                c.showPage()
    c.setTitle("Etiquetas")
    c.save()
    return buffer.getvalue()


def resposta_etiquetas(request, insumo, parametros):
    """HttpResponse (PDF inline) a partir dos parâmetros JÁ validados; audita a geração."""
    dados = {
        "nome": insumo.nome, "lote": parametros["lote"], "emissao": parametros["emissao"], "validade": parametros["validade"],
        "url": url_do_qr(request, insumo),
    }
    pdf = gerar_pdf(dados, parametros["copias"], parametros["formato"])
    registrar_auditoria(
        request, acao="exportar", modulo="estoque", tabela=insumo._meta.db_table, registro_id=str(insumo.pk), registro_nome=insumo.nome,
        descricao=f"Etiquetas geradas: {parametros['copias']} do insumo ID {insumo.pk} - {insumo.nome} ({parametros['formato']}).",
        silencioso=False,
    )
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = f'inline; filename="etiquetas-insumo-{insumo.pk}.pdf"'
    return resposta


class EtiquetaForm(EtiquetaParametrosForm):
    """Versão da tela: acrescenta a escolha do insumo (só ativos das unidades que o usuário alcança)."""

    def __init__(self, *args, insumos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["insumo"] = forms.ModelChoiceField(
            queryset=insumos, label="Insumo", empty_label="Selecione...", widget=forms.Select(attrs={"class": "form-select"}),
        )
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nome} ({i.codigo})"
        self.order_fields(["insumo"])

    def clean(self):
        self.insumo = self.cleaned_data.get("insumo")  # a validade calculada depende do insumo escolhido
        return super().clean()
