"""Regras de negócio globais do sistema (Fase 2).

Tudo que é cálculo/validação reutilizável mora aqui — nunca em views,
templates ou JavaScript:

- dinheiro.py     -> centavos <-> Decimal, formatação e leitura de "R$ 1.234,56"
- conversao.py    -> conversão entre unidades de medida e preço por unidade
- extenso.py      -> "100 gramas", "Cem reais"
- validadores.py  -> CPF, CNPJ, telefone, celular, e-mail
- indicadores.py  -> markup, margem, CMV, ponto de equilíbrio etc.

São módulos de funções simples (sem classes desnecessárias) e não dependem
do banco, o que os torna fáceis de testar e de reaproveitar na API.
"""


class RegraNegocioError(ValueError):
    """Erro de validação de uma regra de negócio (entrada inválida, divisão
    por zero, unidades incompatíveis...). As views/API convertem esta
    exceção em mensagem amigável ao usuário."""
