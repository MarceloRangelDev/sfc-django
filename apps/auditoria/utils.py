"""
Helper único para gravar no log de auditoria (RegistroAuditoria), usado
tanto pelos sinais de login/logout (signals.py) quanto pelas views de
negócio (ver apps/insumo/views.py para o padrão a seguir em outros
módulos, quando forem implementados).
"""
import logging

from .models import RegistroAuditoria

logger = logging.getLogger(__name__)


def _obter_ip(request):
    if request is None:
        return None
    encaminhado_por = request.META.get("HTTP_X_FORWARDED_FOR")
    if encaminhado_por:
        return encaminhado_por.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def registrar(request, acao, modulo="", registro_id="", descricao="", resultado=RegistroAuditoria.RESULTADO_SUCESSO, usuario=None):
    """Grava um evento no log de auditoria.

    Nunca deixa uma falha de auditoria derrubar a requisição principal —
    registrar auditoria é um efeito colateral da ação do usuário, não o
    motivo da requisição, então qualquer erro aqui só é logado no console/
    arquivo de log, sem propagar para quem chamou.
    """
    try:
        usuario_final = usuario
        if usuario_final is None and request is not None and request.user.is_authenticated:
            usuario_final = request.user

        cliente = None
        if usuario_final is not None:
            cliente = getattr(getattr(usuario_final, "profile", None), "cliente", None)

        RegistroAuditoria.objects.create(
            usuario=usuario_final,
            cliente=cliente,
            acao=acao,
            modulo=modulo,
            registro_id=str(registro_id) if registro_id else "",
            descricao=descricao,
            ip=_obter_ip(request),
            resultado=resultado,
        )
    except Exception:
        logger.exception("Falha ao gravar registro de auditoria (ação=%s, módulo=%s)", acao, modulo)
