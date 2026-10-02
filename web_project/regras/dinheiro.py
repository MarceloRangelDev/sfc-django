"""Dinheiro: persistência em centavos (BIGINT), cálculo em Decimal.

Regra do projeto (seção 40 do PROMPT_MESTRE): NUNCA usar float para
dinheiro e NUNCA espalhar `valor / 100` ou `valor * 100` pelo código —
toda conversão passa por DinheiroService.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import RegraNegocioError

CENTAVOS_POR_REAL = Decimal("100")
CENTAVO = Decimal("0.01")


def para_decimal_seguro(valor, campo="valor"):
    """Converte int/str/Decimal em Decimal; recusa float.

    float é recusado de propósito: 0.1 + 0.2 != 0.3 em ponto flutuante, e
    o erro silencioso em valores financeiros é exatamente o que queremos
    impedir. Quem tem um float deve converter conscientemente (Decimal(str(x))).
    """
    if isinstance(valor, float):
        raise RegraNegocioError(f"{campo}: float não é permitido em cálculos financeiros; use Decimal.")
    if isinstance(valor, bool) or valor is None:
        raise RegraNegocioError(f"{campo}: informe um número válido.")
    try:
        resultado = valor if isinstance(valor, Decimal) else Decimal(valor)
    except (InvalidOperation, ValueError, TypeError):
        raise RegraNegocioError(f"{campo}: informe um número válido.") from None
    if not resultado.is_finite():
        raise RegraNegocioError(f"{campo}: informe um número válido.")
    return resultado


def ler_numero_brasileiro(texto):
    """Lê '1234.56', '1234,56', '1.234,56' ou 'R$ 1.234,56' e devolve Decimal.

    Nunca confia no formato exato vindo do frontend: o usuário pode digitar
    com vírgula (padrão brasileiro) ou colar o símbolo "R$". Regras:
    - com vírgula: ponto é milhar e vírgula é decimal ('1.234,56');
    - só ponto: tratado como decimal ('1234.56'), formato do JSON/API.
    """
    if isinstance(texto, (Decimal, int)) and not isinstance(texto, bool):
        return Decimal(texto)
    if texto is None or isinstance(texto, (float, bool)):
        raise RegraNegocioError("Informe um número válido.")
    limpo = str(texto).strip().replace("R$", "").replace(" ", "").replace("\xa0", "")
    if not limpo:
        raise RegraNegocioError("Informe um número válido.")
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(limpo)
    except InvalidOperation:
        raise RegraNegocioError("Informe um número válido.") from None
    if not numero.is_finite():
        raise RegraNegocioError("Informe um número válido.")
    return numero


class DinheiroService:
    """Conversões monetárias centralizadas."""

    @staticmethod
    def para_decimal(centavos):
        """Centavos (int) -> Decimal em reais. 400 -> Decimal('4.00')."""
        if centavos is None:
            return None
        return (Decimal(int(centavos)) / CENTAVOS_POR_REAL).quantize(CENTAVO)

    @staticmethod
    def para_centavos(valor):
        """Reais (Decimal/str/int) -> centavos (int), arredondando meio-centavo
        para cima (ROUND_HALF_UP, o arredondamento comercial esperado)."""
        decimal = para_decimal_seguro(valor, "valor")
        return int((decimal * CENTAVOS_POR_REAL).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def de_texto(texto):
        """Texto digitado pelo usuário ('R$ 1.234,56') -> centavos (int)."""
        return DinheiroService.para_centavos(ler_numero_brasileiro(texto))

    @staticmethod
    def formatar(centavos):
        """Centavos -> 'R$ 1.234,56' (sem depender do locale do SO)."""
        if centavos is None:
            return "-"
        return DinheiroService.formatar_decimal(DinheiroService.para_decimal(centavos))

    @staticmethod
    def formatar_decimal(valor):
        """Decimal em reais -> 'R$ 1.234,56'."""
        if valor is None:
            return "-"
        texto = f"{para_decimal_seguro(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP):,.2f}"
        return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def para_api(centavos):
        """Padrão único da API: inteiro em centavos + string decimal + texto
        formatado (nunca float)."""
        if centavos is None:
            return None
        return {
            "centavos": int(centavos),
            "decimal": str(DinheiroService.para_decimal(centavos)),
            "formatado": DinheiroService.formatar(centavos),
        }
