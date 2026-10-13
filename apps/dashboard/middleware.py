import logging
import time

from .presenca import CHAVE_SESSAO, INTERVALO_GRAVACAO, registrar_atividade

logger = logging.getLogger(__name__)


class PresencaMiddleware:
    """Marca a atividade do usuário autenticado (ver presenca.py). Arquivos estáticos
    não passam por aqui (servidos antes pelo WhiteNoise). Qualquer falha é registrada
    em log e NUNCA derruba a requisição."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if usuario is not None and usuario.is_authenticated:
            try:
                agora = time.time()
                if agora - request.session.get(CHAVE_SESSAO, 0) >= INTERVALO_GRAVACAO:
                    registrar_atividade(usuario)
                    request.session[CHAVE_SESSAO] = agora
            except Exception:  # noqa: BLE001 — presença é acessória
                logger.exception("Falha ao registrar a presença do usuário %s", getattr(usuario, "pk", None))
        return self.get_response(request)
