"""Validações reutilizáveis (backend). A máscara do frontend NÃO substitui
estas funções: qualquer POST direto precisa passar por aqui também.

CPF/CNPJ continuam implementados em apps/cliente/validators.py (já usados
por Cliente.clean()); são apenas reexportados para existir um único ponto
de importação das validações do sistema.
"""
import re
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.cliente.validators import (  # noqa: F401  (reexport)
    formatar_documento,
    normalizar_documento,
    validar_cnpj,
    validar_cpf,
)

from . import RegraNegocioError

# Reexporta as validações de CPF/CNPJ para existir um único ponto de importação.
__all__ = [
    "formatar_documento", "normalizar_documento", "validar_cnpj", "validar_cpf",
    "somente_digitos", "validar_telefone", "validar_celular", "formatar_telefone", "validar_email",
    "ler_data_br", "ler_datahora_br", "formatar_data_br",
]


def somente_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def _ddd_valido(ddd):
    # DDDs brasileiros vão de 11 a 99 (não existe DDD começando com 0 ou
    # terminando em 0).
    return len(ddd) == 2 and ddd[0] != "0" and ddd[1] != "0"


def validar_telefone(telefone):
    """Telefone fixo brasileiro: DDD + 8 dígitos começando por 2 a 5."""
    digitos = somente_digitos(telefone)
    return len(digitos) == 10 and _ddd_valido(digitos[:2]) and digitos[2] in "2345"


def validar_celular(celular):
    """Celular brasileiro: DDD + 9 dígitos começando por 9."""
    digitos = somente_digitos(celular)
    return len(digitos) == 11 and _ddd_valido(digitos[:2]) and digitos[2] == "9"


def formatar_telefone(telefone):
    """'11987654321' -> '(11) 98765-4321'; '1134567890' -> '(11) 3456-7890'."""
    digitos = somente_digitos(telefone)
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return telefone or ""


def validar_email(email):
    """True se o e-mail tem formato válido (usa o validador nativo do Django)."""
    try:
        validate_email((email or "").strip())
    except ValidationError:
        return False
    return True


def ler_data_br(texto):
    """'31/12/2025' -> date(2025, 12, 31). Datas inexistentes ('31/02/2025')
    são rejeitadas."""
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        raise RegraNegocioError("Informe uma data válida (dd/mm/aaaa).") from None


def ler_datahora_br(texto):
    """'31/12/2025 23:59' -> datetime."""
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y %H:%M")
    except ValueError:
        raise RegraNegocioError("Informe data e hora válidas (dd/mm/aaaa hh:mm).") from None


def formatar_data_br(valor):
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return ""
