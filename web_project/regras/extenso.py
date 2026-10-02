"""Textos "por extenso": quantidade com unidade ('100 gramas') e valores
em reais ('Cem reais'). Funções puras, sem banco."""
from . import RegraNegocioError
from .dinheiro import para_decimal_seguro

_UNIDADES = [
    "zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
    "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove",
]
_DEZENAS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_CENTENAS = [
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
    "seiscentos", "setecentos", "oitocentos", "novecentos",
]
# (singular, plural) de cada grupo de 3 dígitos, do menor para o maior.
_ESCALAS = [("", ""), ("mil", "mil"), ("milhão", "milhões"), ("bilhão", "bilhões"), ("trilhão", "trilhões")]


def _ate_999(n):
    """Escreve 1..999 por extenso ('cento e vinte e três')."""
    if n == 100:
        return "cem"
    partes = []
    centena, resto = divmod(n, 100)
    if centena:
        partes.append(_CENTENAS[centena])
    if resto:
        if resto < 20:
            partes.append(_UNIDADES[resto])
        else:
            dezena, unidade = divmod(resto, 10)
            partes.append(_DEZENAS[dezena] + (f" e {_UNIDADES[unidade]}" if unidade else ""))
    return " e ".join(partes)


def numero_por_extenso(n):
    """Inteiro >= 0 por extenso: 1234 -> 'mil duzentos e trinta e quatro'."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise RegraNegocioError("Informe um número inteiro não negativo.")
    if n == 0:
        return "zero"
    if n >= 10 ** (3 * len(_ESCALAS)):
        raise RegraNegocioError("Número grande demais para escrever por extenso.")

    grupos = []  # (valor_do_grupo, indice_da_escala), do maior para o menor
    indice = 0
    while n:
        n, valor = divmod(n, 1000)
        if valor:
            grupos.append((valor, indice))
        indice += 1
    grupos.reverse()

    textos = []
    for valor, escala in grupos:
        if escala == 1 and valor == 1:
            textos.append("mil")  # "mil", nunca "um mil"
            continue
        nome = _ESCALAS[escala][0 if valor == 1 else 1]
        textos.append((_ate_999(valor) + (f" {nome}" if nome else "")).strip())

    # Conector "e" só antes do ÚLTIMO grupo, e apenas quando ele é < 100 ou
    # múltiplo de 100 ('mil e cem', 'dois milhões e um'); caso contrário,
    # só espaço ('mil duzentos e trinta').
    resultado = textos[0]
    for i in range(1, len(textos)):
        ultimo = i == len(textos) - 1
        valor = grupos[i][0]
        usar_e = ultimo and (valor < 100 or valor % 100 == 0)
        resultado += (" e " if usar_e else " ") + textos[i]
    return resultado


def formatar_numero_br(valor):
    """Decimal -> texto brasileiro sem zeros inúteis: 100 -> '100', 0.5 -> '0,5',
    1234.5 -> '1.234,5'."""
    numero = para_decimal_seguro(valor, "quantidade")
    texto = format(numero.normalize(), "f")
    if "." in texto:
        inteiro, decimal = texto.split(".")
    else:
        inteiro, decimal = texto, ""
    inteiro = f"{int(inteiro):,}".replace(",", ".") if inteiro.lstrip("-").isdigit() else inteiro
    return f"{inteiro},{decimal}" if decimal else inteiro


def quantidade_por_extenso(quantidade, singular, plural):
    """'100' + grama/gramas -> '100 gramas'; '1' -> '1 grama'; '0,5' -> '0,5 litro'.

    Singular quando 0 < |quantidade| <= 1 (regra usual do português: '0,5 litro',
    '1 litro', '1,5 litros'); plural nos demais casos, inclusive zero.
    """
    numero = para_decimal_seguro(quantidade, "quantidade")
    nome = singular if 0 < abs(numero) <= 1 else plural
    return f"{formatar_numero_br(numero)} {nome}"


def plural_padrao(nome):
    """Plural de um nome de medida quando não houver um cadastrado.

    Pluraliza as palavras antes de uma preposição ('de', 'da'...): 'Metro
    cúbico' -> 'metros cúbicos'; 'Quilômetro' -> 'quilômetros'. Casos
    irregulares ('colher de sopa' -> 'colheres de sopa') devem ser
    cadastrados no campo nome_plural da Medida — isto é só uma rede de
    segurança para medidas novas.
    """
    preposicoes = {"de", "da", "do", "das", "dos"}
    palavras = nome.strip().lower().split()
    resultado = []
    plural_aplicado = False
    for palavra in palavras:
        if palavra in preposicoes:
            resultado.append(palavra)
            plural_aplicado = True  # tudo depois da preposição fica como está
            continue
        if plural_aplicado or palavra.endswith("s"):
            resultado.append(palavra)
        elif palavra.endswith(("r", "z")):
            resultado.append(palavra + "es")
        elif palavra.endswith("ão"):
            resultado.append(palavra[:-2] + "ões")
        elif palavra.endswith("m"):
            resultado.append(palavra[:-1] + "ns")
        else:
            resultado.append(palavra + "s")
    return " ".join(resultado)


def moeda_por_extenso(valor_centavos):
    """Centavos -> reais por extenso: 10000 -> 'Cem reais'; 150 -> 'Um real e
    cinquenta centavos'; 100000000 -> 'Um milhão de reais'."""
    if isinstance(valor_centavos, bool) or not isinstance(valor_centavos, int):
        raise RegraNegocioError("Informe o valor em centavos (inteiro).")
    centavos_total = valor_centavos
    if centavos_total < 0:
        raise RegraNegocioError("Valor negativo não pode ser escrito por extenso.")
    reais, centavos = divmod(centavos_total, 100)

    partes = []
    if reais or not centavos:
        texto = numero_por_extenso(reais)
        # "um milhão DE reais": sem resto abaixo de milhão, usa-se "de".
        de = " de" if reais and reais % 1_000_000 == 0 else ""
        partes.append(f"{texto}{de} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{numero_por_extenso(centavos)} {'centavo' if centavos == 1 else 'centavos'}")
    texto = " e ".join(partes)
    return texto[0].upper() + texto[1:]
