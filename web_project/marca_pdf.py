"""Logo do sistema nos PDFs: UM lugar só. Todo PDF gerado pela aplicação chama `desenhar_logo` no canto SUPERIOR ESQUERDO
do cabeçalho (relatórios, documentos de ficha/processado e comprovante). Arquivo: src/assets/img/branding/logo-relatorio.png."""
import logging
from pathlib import Path

from django.conf import settings
from reportlab.lib.utils import ImageReader

logger = logging.getLogger(__name__)
ARQUIVO_LOGO = Path(settings.BASE_DIR) / "src" / "assets" / "img" / "branding" / "logo-relatorio.png"
_leitor = None


def _logo():
    global _leitor
    if _leitor is None:
        try:
            _leitor = ImageReader(str(ARQUIVO_LOGO))
        except Exception:  # arquivo ausente/ilegível nunca pode impedir a geração do PDF
            logger.warning("Logo dos PDFs indisponível: %s", ARQUIVO_LOGO)
            _leitor = False
    return _leitor or None


def desenhar_logo(canvas_, x, topo, lado):
    """Desenha o logo (quadrado de `lado` pontos) com o canto superior esquerdo em (x, topo)."""
    logo = _logo()
    if logo is not None:
        canvas_.drawImage(logo, x, topo - lado, width=lado, height=lado, mask="auto", preserveAspectRatio=True)
