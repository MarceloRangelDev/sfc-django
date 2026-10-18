"""Valor por mês: hora, dia, semana e quinzena derivados (calculo.derivados_do_mes), com base configurável da unidade."""
from decimal import Decimal as D

from django.urls import reverse

from web_project.regras import RegraNegocioError

from . import calculo as c
from .models import Apontamento, Colaborador, ConfiguracaoMaoDeObra
from .tests import Base


class ValorMensalTests(Base):
    def test_derivados_padrao(self):
        # R$ 3.000,00/mês, 220 h, 30 dias: hora 13,64 | dia 100,00 | semana 700,00 | quinzena 1.500,00
        self.assertEqual(c.derivados_do_mes(300000, D("220"), D("30")), {"hora": 1364, "dia": 10000, "semana": 70000, "quinzena": 150000})

    def test_base_configuravel(self):
        self.assertEqual(c.derivados_do_mes(264000, D("176"), D("22"))["hora"], 1500)
        with self.assertRaises(RegraNegocioError):
            c.derivados_do_mes(100000, D("0"), D("30"))

    def test_cadastro_com_valor_mensal_calcula_hora_e_dia_no_servidor(self):
        resposta = self.client.post(reverse("colaborador-create"), {"nome": "Bia", "funcao": "Cozinheira", "valor_mes": "3.000,00", "valor_hora": "1,00", "valor_dia": "1,00", "ativo": "on"})
        self.assertEqual(resposta.status_code, 302)
        bia = Colaborador.objects.get(nome="Bia")
        self.assertEqual((bia.valor_mes, bia.valor_hora, bia.valor_dia), (300000, 1364, 10000))  # o que veio nos campos é ignorado
        self.assertEqual(bia.derivados()["semana"], 70000)
        lista = self.client.get(reverse("colaborador-list")).content.decode()
        self.assertIn("R$ 700,00", lista)
        self.assertIn("R$ 1.500,00", lista)

    def test_sem_valor_mensal_exige_a_hora(self):
        resposta = self.client.post(reverse("colaborador-create"), {"nome": "Caio", "ativo": "on"})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Colaborador.objects.filter(nome="Caio").exists())

    def test_regras_do_mes_seguem_a_configuracao_da_unidade(self):
        config = ConfiguracaoMaoDeObra.da_unidade(self.cliente)
        config.horas_mes, config.dias_mes = D("176"), D("22")
        config.save()
        self.client.post(reverse("colaborador-create"), {"nome": "Duda", "valor_mes": "2.640,00", "ativo": "on"})
        self.assertEqual(Colaborador.objects.get(nome="Duda").valor_hora, 1500)

    def test_apontamento_usa_a_hora_derivada(self):
        self.client.post(reverse("colaborador-create"), {"nome": "Eva", "valor_mes": "4.400,00", "ativo": "on"})  # hora R$ 20,00
        eva = Colaborador.objects.get(nome="Eva")
        self.assertEqual(eva.valor_hora, 2000)
        self.client.post(reverse("apontamento-create"), self.dados(colaborador=eva.pk))
        self.assertEqual(Apontamento.objects.get().valor_total, 20500)
