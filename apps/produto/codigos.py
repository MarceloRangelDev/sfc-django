"""Código de barras e QR Code dos produtos.

Todo produto tem um `codigo_barras`: o que o usuário digitar (ex.: o EAN do fabricante) ou, se deixar em branco, um
EAN-13 interno gerado pelo sistema (prefixo 2 = uso interno da loja, + id do produto + dígito verificador), que
qualquer leitor comum entende. O QR Code carrega o mesmo texto, então os dois levam ao mesmo produto no PDV.
As imagens são SVG geradas com o reportlab (sem dependência nova)."""
import re

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing

PADRAO_CODIGO = re.compile(r"^[A-Za-z0-9._\-]{1,32}$")


def digito_ean13(doze):
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doze))
    return str((10 - soma % 10) % 10)


def codigo_interno(pk):
    """EAN-13 interno: '2' + id com 11 dígitos + dígito verificador."""
    base = f"2{pk:011d}"
    return base + digito_ean13(base)


def ean13_valido(codigo):
    return len(codigo) == 13 and codigo.isdigit() and digito_ean13(codigo[:12]) == codigo[12]


def _svg(desenho):
    return renderSVG.drawToString(desenho)


def svg_barras(codigo, altura=60):
    """Código de barras: EAN-13 quando o texto é um EAN válido, senão Code 128."""
    tipo = "EAN13" if ean13_valido(codigo) else "Code128"
    desenho = createBarcodeDrawing(tipo, value=codigo, barHeight=altura, humanReadable=True, quiet=True)
    return _svg(desenho)


def svg_qr(codigo, lado=140):
    desenho = createBarcodeDrawing("QR", value=codigo, barWidth=lado, barHeight=lado, barBorder=2)
    return _svg(desenho)
