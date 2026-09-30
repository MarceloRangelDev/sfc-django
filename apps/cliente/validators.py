"""
Validação real de CPF e CNPJ (conferência matemática dos dígitos
verificadores), não apenas de formato/máscara.

Usado por Cliente.clean() (apps/cliente/models.py) para garantir que
nenhum CPF/CNPJ inválido chegue a ser gravado no banco, mesmo que a
máscara do campo no formulário seja manipulada ou ignorada (ex: alguém
enviando um POST direto para a view, sem passar pelo JavaScript do
formulário).
"""
import re


def normalizar_documento(documento):
    """Remove tudo que não for dígito (pontos, traço, barra, espaços).

    Aceita tanto "123.456.789-09" quanto "12345678909" como entrada —
    o valor gravado no banco é sempre a versão só com dígitos, para nunca
    guardar o mesmo documento em dois formatos diferentes.
    """
    return re.sub(r"\D", "", documento or "")


def _digito_verificador(parcial, pesos):
    """Calcula um dígito verificador (usado tanto para CPF quanto CNPJ).

    `parcial` são os dígitos já conhecidos do documento e `pesos` os
    multiplicadores oficiais de cada posição (Receita Federal).
    """
    soma = sum(int(digito) * peso for digito, peso in zip(parcial, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def validar_cpf(cpf):
    """Retorna True se `cpf` (11 dígitos, já normalizado) é matematicamente
    válido — confere os dois dígitos verificadores oficiais."""
    cpf = normalizar_documento(cpf)
    if len(cpf) != 11:
        return False
    # CPFs com todos os dígitos iguais (ex: "111.111.111-11") "passam" no
    # cálculo do dígito verificador mas nunca são documentos reais válidos.
    if cpf == cpf[0] * 11:
        return False

    pesos_d1 = list(range(10, 1, -1))       # 10,9,8,...,2
    pesos_d2 = list(range(11, 1, -1))       # 11,10,9,...,2

    d1 = _digito_verificador(cpf[:9], pesos_d1)
    d2 = _digito_verificador(cpf[:9] + str(d1), pesos_d2)

    return cpf[-2:] == f"{d1}{d2}"


def validar_cnpj(cnpj):
    """Retorna True se `cnpj` (14 dígitos, já normalizado) é matematicamente
    válido — confere os dois dígitos verificadores oficiais."""
    cnpj = normalizar_documento(cnpj)
    if len(cnpj) != 14:
        return False
    if cnpj == cnpj[0] * 14:
        return False

    pesos_d1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_d2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    d1 = _digito_verificador(cnpj[:12], pesos_d1)
    d2 = _digito_verificador(cnpj[:12] + str(d1), pesos_d2)

    return cnpj[-2:] == f"{d1}{d2}"


def formatar_documento(documento, tipo_pessoa):
    """Formata um documento já normalizado com a máscara usual, só para
    exibição (o valor gravado no banco continua sem máscara)."""
    documento = normalizar_documento(documento)
    if tipo_pessoa == "PF" and len(documento) == 11:
        return f"{documento[0:3]}.{documento[3:6]}.{documento[6:9]}-{documento[9:11]}"
    if tipo_pessoa == "PJ" and len(documento) == 14:
        return f"{documento[0:2]}.{documento[2:5]}.{documento[5:8]}/{documento[8:12]}-{documento[12:14]}"
    return documento
