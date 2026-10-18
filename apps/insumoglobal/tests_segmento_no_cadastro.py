"""Segmento definido já no cadastro do insumo global (criação), inclusive ao acrescentar uma marca a um padrão existente."""
from django.urls import reverse

from apps.modulo.models import Segmento

from .models import InsumoGlobal
from .tests import Base


class SegmentoNoCadastroTests(Base):
    def setUp(self):
        super().setUp()
        self.produto = Segmento.objects.get(slug="produto")
        self.servico = Segmento.objects.get(slug="servico")
        self.logar(self.admin)

    def criar(self, nome, segmentos, marca=""):
        return self.client.post(reverse("insumo-global-create"), {"nome": nome, "marca": marca, "descricao": "", "segmentos": [s.pk for s in segmentos], "ativo": "on"})

    def test_cria_ja_com_os_segmentos_escolhidos(self):
        self.assertEqual(self.criar("Fermento", [self.produto, self.servico]).status_code, 302)
        self.assertEqual({s.slug for s in InsumoGlobal.objects.get(nome="Fermento").segmentos.all()}, {"produto", "servico"})

    def test_criar_com_marca_tambem_grava_os_segmentos(self):
        self.criar("Fermento", [self.servico], marca="Fleischmann")
        fermento = InsumoGlobal.objects.get(nome="Fermento")
        self.assertEqual(list(fermento.segmentos.all()), [self.servico])
        self.assertEqual(fermento.variacoes.count(), 1)

    def test_mesmo_nome_com_outra_marca_acrescenta_os_segmentos_marcados(self):
        self.acucar.segmentos.set([self.produto])
        self.criar("Açúcar", [self.servico], marca="União")
        self.assertEqual({s.slug for s in self.acucar.segmentos.all()}, {"produto", "servico"})  # mantém os atuais e soma os novos

    def test_pagina_de_criacao_mostra_a_escolha_de_segmentos(self):
        pagina = self.client.get(reverse("insumo-global-create")).content.decode()
        self.assertIn('name="segmentos"', pagina)
        self.assertIn("Produto", pagina)
