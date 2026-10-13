from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import TourPreferencia

SENHA = "SenhaForte123!"


class TourPreferenciaTests(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user(username="ana", email="ana@teste.local", password=SENHA)
        self.beto = User.objects.create_user(username="beto", email="beto@teste.local", password=SENHA)

    def estado(self, pagina="insumos"):
        return self.client.get(reverse("tour-estado", args=[pagina])).json()["ocultar"]

    def test_tour_aparece_ate_o_usuario_pedir_para_nao_mostrar(self):
        self.client.login(username="ana", password=SENHA)
        self.assertFalse(self.estado())
        self.assertEqual(self.client.post(reverse("tour-ocultar", args=["insumos"])).status_code, 200)
        self.assertTrue(self.estado())

    def test_preferencia_e_por_usuario_e_por_pagina(self):
        self.client.login(username="ana", password=SENHA)
        self.client.post(reverse("tour-ocultar", args=["insumos"]))
        self.assertFalse(self.estado("categorias"))
        self.client.login(username="beto", password=SENHA)
        self.assertFalse(self.estado("insumos"))

    def test_ocultar_duas_vezes_nao_duplica(self):
        self.client.login(username="ana", password=SENHA)
        for _ in range(2):
            self.client.post(reverse("tour-ocultar", args=["insumos"]))
        self.assertEqual(TourPreferencia.objects.count(), 1)

    def test_exige_login_e_post(self):
        self.assertEqual(self.client.post(reverse("tour-ocultar", args=["insumos"])).status_code, 302)
        self.client.login(username="ana", password=SENHA)
        self.assertEqual(self.client.get(reverse("tour-ocultar", args=["insumos"])).status_code, 405)

    def test_pagina_invalida_nao_casa_com_a_rota(self):
        self.client.login(username="ana", password=SENHA)
        self.assertEqual(self.client.post("/tour/pagina com espaco/ocultar").status_code, 404)
