from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class TokenComExpiracao(TokenAuthentication):
    """Token do DRF (`Authorization: Bearer <token>`) que expira.

    O token nasce no login (apps/api/views.py) e vale API_TOKEN_TTL_DIAS
    dias; depois disso o app mobile precisa fazer login de novo. Cada login
    gera um token novo e invalida o anterior; o logout apaga o token.
    Usuário inativo é recusado mesmo com token válido (a classe base já
    faz essa checagem).
    """

    keyword = "Bearer"

    def authenticate_credentials(self, key):
        usuario, token = super().authenticate_credentials(key)
        validade = timedelta(days=settings.API_TOKEN_TTL_DIAS)
        if token.created + validade < timezone.now():
            token.delete()
            raise AuthenticationFailed("Token expirado. Faça login novamente.")
        return usuario, token


class TokenComExpiracaoScheme(OpenApiAuthenticationExtension):
    """Descreve o esquema de autenticação no Swagger (botão Authorize)."""

    target_class = "apps.api.authentication.TokenComExpiracao"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "Token obtido em POST /api/v1/auth/login/."}
