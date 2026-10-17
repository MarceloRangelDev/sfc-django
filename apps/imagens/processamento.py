"""Validação do conteúdo real da imagem e geração das variantes (Pillow).

Segurança: o formato é decidido pelo CONTEÚDO (não pela extensão nem pelo Content-Type); SVG e demais formatos são recusados; o número
de pixels é checado ANTES de decodificar (bomba de descompressão); as variantes são re-codificadas, o que descarta EXIF/GPS.
O ORIGINAL é guardado como enviado (sem compressão destrutiva) — só as variantes têm perdas (qualidade alta, mas NÃO são sem perdas).
"""
import hashlib
import io

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

FORMATOS_ENTRADA = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}

# Variantes FIXAS (clientes não escolhem tamanho/qualidade): nome -> (lado máximo em px, qualidade, recorte quadrado?).
VARIANTES = {
    "avatar": (96, 85, True),
    "miniatura": (160, 80, True),
    "lista": (400, 80, False),
    "detalhe": (1200, 85, False),
}


class ImagemInvalida(Exception):
    """Arquivo recusado; a mensagem é exibida ao usuário."""


def validar(dados):
    """Valida o arquivo enviado e devolve (mime, largura, altura, checksum). Levanta ImagemInvalida."""
    if not dados:
        raise ImagemInvalida("Arquivo vazio.")
    if len(dados) > settings.IMAGENS_MAX_BYTES:
        raise ImagemInvalida(f"A imagem excede o limite de {settings.IMAGENS_MAX_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(dados)) as img:
            formato, (largura, altura) = img.format, img.size  # lê só o cabeçalho
            if formato not in FORMATOS_ENTRADA:
                raise ImagemInvalida("Formato não permitido. Envie JPEG, PNG ou WebP.")
            if max(largura, altura) > settings.IMAGENS_MAX_LADO or largura * altura > settings.IMAGENS_MAX_PIXELS:
                raise ImagemInvalida(f"Dimensões acima do limite ({settings.IMAGENS_MAX_LADO}px por lado).")
            if getattr(img, "n_frames", 1) > 1:
                raise ImagemInvalida("Imagens animadas não são permitidas.")
            img.load()  # decodifica tudo: detecta arquivo corrompido/truncado
    except ImagemInvalida:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ImagemInvalida("Arquivo de imagem inválido ou corrompido.") from None
    return FORMATOS_ENTRADA[formato][0], largura, altura, hashlib.sha256(dados).hexdigest()


def aceita_avif():
    from PIL import features

    return bool(features.check("avif"))


def formato_saida(cabecalho_accept):
    """Negociação de formato: AVIF se o navegador aceitar e o Pillow suportar; senão WebP (suportado por todos os navegadores atuais)."""
    if "image/avif" in (cabecalho_accept or "") and aceita_avif():
        return "AVIF", "image/avif"
    return "WEBP", "image/webp"


def gerar_variante(dados, variante, formato="WEBP"):
    """Redimensiona sem ampliar, aplica a orientação EXIF e re-codifica (descarta metadados). Devolve bytes."""
    lado, qualidade, quadrado = VARIANTES[variante]
    with Image.open(io.BytesIO(dados)) as img:
        img = ImageOps.exif_transpose(img)  # gira conforme a câmera ANTES de perder o EXIF
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
        if quadrado:
            lado_final = min(lado, *img.size)  # recorte central quadrado, sem ampliar
            img = ImageOps.fit(img, (lado_final, lado_final), Image.LANCZOS)
        else:
            img.thumbnail((lado, lado), Image.LANCZOS)  # nunca amplia
        saida = io.BytesIO()
        img.save(saida, format=formato, quality=qualidade)  # sem exif=...: nenhum metadado é copiado
        return saida.getvalue()
