"""Valor mínimo a cobrar por período (apps/custo/valor_minimo.py)."""
from django.test import SimpleTestCase, TestCase

from apps.cliente.models import Cliente
from apps.maodeobra.models import Colaborador, ConfiguracaoMaoDeObra
from apps.modulo.models import Segmento
from web_project.regras import RegraNegocioError

from .models import CustoOperacional
from .valor_minimo import valor_minimo_da_unidade, valores_minimos


class FormulasTests(SimpleTestCase):
    def test_exemplo_numerico(self):
        # R$ 3.000,00 / 220 h / 30 dias: hora 13,64 (13,636 arredonda p/ cima), dia 100,00, semana 700,00, quinzena 1.500,00
        v = valores_minimos(300000, 220, 30)
        self.assertEqual(v, {"mensal": 300000, "quinzenal": 150000, "semanal": 70000, "diario": 10000, "hora": 1364})

    def test_arredonda_meio_para_cima_uma_vez(self):
        self.assertEqual(valores_minimos(1001, 220, 30)["quinzenal"], 501)  # 500,5 -> 501

    def test_sem_custo_e_base_invalida(self):
        self.assertEqual(valores_minimos(0, 220, 30), {"mensal": 0, "quinzenal": 0, "semanal": 0, "diario": 0, "hora": 0})
        with self.assertRaises(RegraNegocioError):
            valores_minimos(100, 0, 30)


def unidade(cnpj, tipo):
    seg, _ = Segmento.objects.get_or_create(slug=f"t-{tipo}", defaults={"nome": f"T {tipo}", "tipo_precificacao": tipo})
    return Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=f"U{cnpj}", segmento=seg)


class UnidadeTests(TestCase):
    def test_servico_usa_config_da_unidade_e_isola_tenants(self):
        a, b = unidade("11111111000191", "servico"), unidade("22222222000191", "servico")
        CustoOperacional.objects.create(cliente=a, nome="Aluguel", valor=300000)
        CustoOperacional.objects.create(cliente=b, nome="Outro", valor=999900)
        c = ConfiguracaoMaoDeObra.da_unidade(a)
        c.dias_mes = 20
        c.save()
        r = valor_minimo_da_unidade(a)
        self.assertEqual(r["valores"]["mensal"], 300000)  # só o custo da própria unidade
        self.assertEqual(r["valores"]["diario"], 15000)  # 3000 / 20 dias
        self.assertFalse(r["sem_custo"])

    def test_produto_nao_tem_indicador_e_sem_custo_sinaliza(self):
        self.assertEqual(valor_minimo_da_unidade(unidade("33333333000191", "produto")), {})
        self.assertTrue(valor_minimo_da_unidade(unidade("44444444000191", "ambos"))["sem_custo"])

    def test_incluir_mao_de_obra(self):
        a = unidade("55555555000191", "servico")
        CustoOperacional.objects.create(cliente=a, nome="Aluguel", valor=100000)
        Colaborador.objects.create(cliente=a, nome="Ana", valor_mes=200000, valor_hora=1000)
        self.assertEqual(valor_minimo_da_unidade(a)["valores"]["mensal"], 100000)
        self.assertEqual(valor_minimo_da_unidade(a, incluir_mao_de_obra=True)["valores"]["mensal"], 300000)
