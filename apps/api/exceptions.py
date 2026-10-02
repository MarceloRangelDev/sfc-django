import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from web_project.regras import RegraNegocioError

logger = logging.getLogger(__name__)


class RegraDeNegocioViolada(exceptions.APIException):
    """Entrada bem formada, mas que viola uma regra de negócio (ex.: alterar
    o fator de uma medida já em uso). Responde 422."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "A operação viola uma regra de negócio."
    default_code = "regra_de_negocio"


class Conflito(exceptions.APIException):
    """Registro duplicado / conflito de estado. Responde 409."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflito com o estado atual do registro."
    default_code = "conflito"


MENSAGENS = {
    400: "Requisição inválida.",
    401: "Autenticação necessária ou inválida.",
    403: "Você não tem permissão para esta operação.",
    404: "Registro não encontrado.",
    405: "Método não permitido.",
    409: "Conflito: o registro já existe ou está em um estado incompatível.",
    415: "Formato de conteúdo não suportado. Envie JSON.",
    422: "A operação viola uma regra de negócio.",
    429: "Muitas requisições. Tente novamente mais tarde.",
    500: "Erro interno do servidor.",
}


def _codigos(detalhe_codigos):
    """Todos os códigos de erro (recursivo) de um ValidationError.get_codes()."""
    if isinstance(detalhe_codigos, dict):
        for valor in detalhe_codigos.values():
            yield from _codigos(valor)
    elif isinstance(detalhe_codigos, (list, tuple)):
        for valor in detalhe_codigos:
            yield from _codigos(valor)
    else:
        yield detalhe_codigos


def _resposta(codigo, mensagem=None, errors=None):
    return Response(
        {"success": False, "message": mensagem or MENSAGENS.get(codigo, "Erro."), "errors": errors or {}},
        status=codigo,
    )


def tratador_de_excecoes(exc, context):
    """Converte qualquer erro no formato {"success": false, "message", "errors"}
    com o código HTTP adequado e NUNCA vaza detalhes internos num erro 500."""
    # Erros "de Django" viram erros de API equivalentes.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, RegraNegocioError):
        exc = RegraDeNegocioViolada(str(exc))
    elif isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)

    resposta = exception_handler(exc, context)
    if resposta is None:
        logger.exception("Erro não tratado na API", exc_info=exc)
        return _resposta(500)

    codigo = resposta.status_code
    if isinstance(exc, exceptions.ValidationError):
        # Códigos "unique"/"conflito" nos erros de campo => 409 em vez de 400.
        if any(c in ("unique", "conflito") for c in _codigos(exc.get_codes())):
            codigo = 409
        errors = resposta.data
        if isinstance(errors, list):
            errors = {"non_field_errors": errors}
        return _resposta(codigo, errors=errors)

    detalhe = resposta.data.get("detail") if isinstance(resposta.data, dict) else None
    mensagem = str(detalhe) if detalhe and codigo not in (401, 403, 404, 405) else None
    return _resposta(codigo, mensagem=mensagem, errors={"detail": str(detalhe)} if detalhe else None)
