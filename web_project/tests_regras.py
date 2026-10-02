"""Testes das regras globais (Fase 2): dinheiro, conversão, extenso,
validadores e indicadores. Não usam banco."""
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from web_project.regras import RegraNegocioError, conversao, extenso, indicadores, validadores
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro

D = Decimal


def medida(dimensao, fator):
    return SimpleNamespace(dimensao=dimensao, fator_base=D(fator))


GRAMA = medida("massa", "1")
QUILO = medida("massa", "1000")
ML = medida("volume", "1")
LITRO = medida("volume", "1000")
UNIDADE = medida("unidade", "1")
COLHER_SOPA = medida("volume", "15")


class DinheiroTests(SimpleTestCase):
    def test_exemplos_do_prompt(self):
        for reais, centavos in [("4.00", 400), ("40.00", 4000), ("1250.75", 125075), ("0.05", 5)]:
            self.assertEqual(DinheiroService.para_centavos(D(reais)), centavos)
            self.assertEqual(DinheiroService.para_decimal(centavos), D(reais))

    def test_sem_erro_de_ponto_flutuante(self):
        # 0.1 + 0.2 em float daria 0.30000000000000004
        self.assertEqual(DinheiroService.para_centavos(D("0.1") + D("0.2")), 30)

    def test_float_e_recusado(self):
        with self.assertRaises(RegraNegocioError):
            DinheiroService.para_centavos(4.0)

    def test_arredondamento_meio_centavo_para_cima(self):
        self.assertEqual(DinheiroService.para_centavos(D("0.005")), 1)
        self.assertEqual(DinheiroService.para_centavos(D("0.004")), 0)

    def test_formatacao_brasileira(self):
        self.assertEqual(DinheiroService.formatar(125075), "R$ 1.250,75")
        self.assertEqual(DinheiroService.formatar(5), "R$ 0,05")
        self.assertEqual(DinheiroService.formatar(None), "-")

    def test_leitura_de_texto_brasileiro(self):
        for texto, esperado in [("1.234,56", "1234.56"), ("R$ 1.234,56", "1234.56"), ("12,90", "12.90"), ("12.90", "12.90")]:
            self.assertEqual(ler_numero_brasileiro(texto), D(esperado))
        self.assertEqual(DinheiroService.de_texto("R$ 4,00"), 400)

    def test_texto_invalido(self):
        for texto in ["abc", "", "NaN", "Infinity", None]:
            with self.assertRaises(RegraNegocioError):
                ler_numero_brasileiro(texto)

    def test_formato_da_api(self):
        self.assertEqual(
            DinheiroService.para_api(400),
            {"centavos": 400, "decimal": "4.00", "formatado": "R$ 4,00"},
        )


class ConversaoTests(SimpleTestCase):
    def test_exemplos_do_prompt(self):
        self.assertEqual(conversao.converter(1000, GRAMA, QUILO), D("1"))
        self.assertEqual(conversao.converter(100, GRAMA, QUILO), D("0.1"))
        self.assertEqual(conversao.converter(1, QUILO, GRAMA), D("1000"))
        self.assertEqual(conversao.converter(500, ML, LITRO), D("0.5"))

    def test_via_unidade_base(self):
        self.assertEqual(conversao.converter(2, COLHER_SOPA, ML), D("30"))

    def test_dimensoes_diferentes_sao_recusadas(self):
        with self.assertRaises(RegraNegocioError):
            conversao.converter(1, QUILO, LITRO)

    def test_quantidade_invalida(self):
        for quantidade in [-1, "abc", None, D("NaN")]:
            with self.assertRaises(RegraNegocioError):
                conversao.converter(quantidade, GRAMA, QUILO)

    def test_fator_invalido_nao_divide_por_zero(self):
        with self.assertRaises(RegraNegocioError):
            conversao.converter(1, GRAMA, medida("massa", "0"))
        with self.assertRaises(RegraNegocioError):
            conversao.converter(1, GRAMA, None)

    def test_casas_decimais(self):
        self.assertEqual(conversao.converter(1, GRAMA, QUILO, casas=2), D("0.00"))

    def test_preco_por_kg_100g_a_4_reais(self):
        self.assertEqual(conversao.preco_por_unidade_padrao(400, 100, GRAMA), 4000)

    def test_preco_por_litro_500ml_a_10_reais(self):
        self.assertEqual(conversao.preco_por_unidade_padrao(1000, 500, ML), 2000)

    def test_preco_por_unidade_e_por_kg(self):
        self.assertEqual(conversao.preco_por_unidade_padrao(500, 5, UNIDADE), 100)
        self.assertEqual(conversao.preco_por_unidade_padrao(4000, 1, QUILO), 4000)

    def test_preco_com_quantidade_zero_e_recusado(self):
        with self.assertRaises(RegraNegocioError):
            conversao.preco_por_unidade_padrao(400, 0, GRAMA)

    def test_sigla_do_rotulo(self):
        self.assertEqual(conversao.sigla_preco_padrao("massa"), "KG")
        self.assertEqual(conversao.sigla_preco_padrao("volume"), "L")
        self.assertEqual(conversao.sigla_preco_padrao("unidade"), "UN")


class ExtensoTests(SimpleTestCase):
    def test_quantidades(self):
        casos = [
            (100, "grama", "gramas", "100 gramas"),
            (1, "quilo", "quilos", "1 quilo"),
            (500, "mililitro", "mililitros", "500 mililitros"),
            (2, "litro", "litros", "2 litros"),
            (D("0.5"), "litro", "litros", "0,5 litro"),
            (D("1.5"), "litro", "litros", "1,5 litros"),
            (1000, "grama", "gramas", "1.000 gramas"),
        ]
        for quantidade, sing, plur, esperado in casos:
            self.assertEqual(extenso.quantidade_por_extenso(quantidade, sing, plur), esperado)

    def test_numeros(self):
        casos = {
            0: "zero", 1: "um", 15: "quinze", 21: "vinte e um", 100: "cem", 101: "cento e um",
            1000: "mil", 1100: "mil e cem", 1234: "mil duzentos e trinta e quatro",
            2000000: "dois milhões", 2000001: "dois milhões e um",
        }
        for n, texto in casos.items():
            self.assertEqual(extenso.numero_por_extenso(n), texto)

    def test_moeda(self):
        self.assertEqual(extenso.moeda_por_extenso(10000), "Cem reais")
        self.assertEqual(extenso.moeda_por_extenso(100), "Um real")
        self.assertEqual(extenso.moeda_por_extenso(150), "Um real e cinquenta centavos")
        self.assertEqual(extenso.moeda_por_extenso(1), "Um centavo")
        self.assertEqual(extenso.moeda_por_extenso(0), "Zero reais")
        self.assertEqual(extenso.moeda_por_extenso(100000000), "Um milhão de reais")

    def test_moeda_invalida(self):
        for valor in [-1, 1.5, None, "10"]:
            with self.assertRaises(RegraNegocioError):
                extenso.moeda_por_extenso(valor)

    def test_plural_padrao(self):
        self.assertEqual(extenso.plural_padrao("Metro cúbico"), "metros cúbicos")
        self.assertEqual(extenso.plural_padrao("Colher de sopa"), "colheres de sopa")
        self.assertEqual(extenso.plural_padrao("Polegada"), "polegadas")


class ValidadoresTests(SimpleTestCase):
    def test_cpf(self):
        self.assertTrue(validadores.validar_cpf("529.982.247-25"))
        self.assertFalse(validadores.validar_cpf("111.111.111-11"))
        self.assertFalse(validadores.validar_cpf("529.982.247-24"))

    def test_cnpj(self):
        self.assertTrue(validadores.validar_cnpj("11.222.333/0001-81"))
        self.assertFalse(validadores.validar_cnpj("11.222.333/0001-82"))

    def test_telefone_e_celular(self):
        self.assertTrue(validadores.validar_telefone("(11) 3456-7890"))
        self.assertFalse(validadores.validar_telefone("(11) 98765-4321"))
        self.assertTrue(validadores.validar_celular("(11) 98765-4321"))
        self.assertFalse(validadores.validar_celular("(11) 3456-7890"))
        self.assertFalse(validadores.validar_celular("(00) 98765-4321"))
        self.assertEqual(validadores.formatar_telefone("11987654321"), "(11) 98765-4321")

    def test_email(self):
        self.assertTrue(validadores.validar_email("a@b.com"))
        self.assertFalse(validadores.validar_email("a@@b"))
        self.assertFalse(validadores.validar_email(""))

    def test_datas(self):
        self.assertEqual(validadores.ler_data_br("31/12/2025").year, 2025)
        self.assertEqual(validadores.ler_datahora_br("31/12/2025 23:59").minute, 59)
        for texto in ["31/02/2025", "2025-12-31", "", "abc"]:
            with self.assertRaises(RegraNegocioError):
                validadores.ler_data_br(texto)


class IndicadoresTests(SimpleTestCase):
    def test_markup_e_preco(self):
        self.assertEqual(indicadores.markup(D("10"), D("15")), D("50"))
        self.assertEqual(indicadores.preco_por_markup(D("10"), D("50")), D("15"))

    def test_margem_e_preco(self):
        self.assertEqual(indicadores.margem_lucro(D("20"), D("15")), D("25"))
        self.assertEqual(indicadores.preco_por_margem(D("15"), D("25")), D("20"))

    def test_margem_de_100_ou_mais_e_recusada(self):
        with self.assertRaises(RegraNegocioError):
            indicadores.preco_por_margem(D("15"), D("100"))

    def test_lucro_ticket_cmv(self):
        self.assertEqual(indicadores.lucro_produto(D("20"), D("15")), D("5"))
        self.assertEqual(indicadores.ticket_medio(D("1000"), 40), D("25"))
        self.assertEqual(indicadores.cmv(D("100"), D("500"), D("200")), D("400"))

    def test_ponto_de_equilibrio(self):
        # custos fixos 1000, preço 20, custo variável 10 -> margem de contribuição 10 -> 100 un
        self.assertEqual(indicadores.ponto_equilibrio_unidades(D("1000"), D("20"), D("10")), D("100"))
        self.assertEqual(indicadores.ponto_equilibrio_valor(D("1000"), D("20"), D("10")), D("2000"))

    def test_fator_de_coccao_e_reducao(self):
        self.assertEqual(indicadores.fator_coccao(1000, 700), D("0.7"))
        self.assertEqual(indicadores.fator_reducao(1000, 700), D("30"))

    def test_custo_unitario(self):
        self.assertEqual(indicadores.custo_unitario(D("40"), D("4")), D("10"))

    def test_divisao_por_zero_e_entradas_invalidas(self):
        chamadas = [
            lambda: indicadores.markup(0, 10),
            lambda: indicadores.margem_lucro(0, 10),
            lambda: indicadores.ticket_medio(100, 0),
            lambda: indicadores.custo_unitario(100, 0),
            lambda: indicadores.ponto_equilibrio_unidades(100, 10, 10),
            lambda: indicadores.fator_coccao(0, 10),
            lambda: indicadores.lucro_produto(-1, 0),
            lambda: indicadores.cmv(10, 10, 100),
            lambda: indicadores.markup(1.5, 2),
        ]
        for chamada in chamadas:
            with self.assertRaises(RegraNegocioError):
                chamada()
