"""Cadastro de insumo a partir do insumo global: dropdown de marcas do insumo escolhido, "Personalizar" a marca e fila de fora do padrão."""
import json

from django.urls import reverse

from apps.insumo.models import Insumo
from apps.notificacao.models import DestinatarioNotificacao

from . import services
from .models import InsumoGlobal
from .tests import Base


class MarcaDropdownTests(Base):
    def setUp(self):
        super().setUp()
        self.uniao, _ = services.adicionar_marca(None, self.acucar, "União")
        self.caravelas, _ = services.adicionar_marca(None, self.acucar, "Caravelas")

    def cadastrar_com(self, **extra):
        self.logar(self.gestor)
        return self.cadastrar("", insumo_padrao=self.acucar.pk, **extra)

    def avisos(self, titulo):
        return DestinatarioNotificacao.objects.filter(notificacao__titulo=titulo)

    def test_escolher_a_marca_do_dropdown_vincula_a_variacao_sem_aviso(self):
        self.cadastrar_com(marca_padrao=self.uniao.pk)
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.insumo_global, insumo.insumo_global_marca, insumo.marca, insumo.nao_conforme), (self.acucar, self.uniao, "União", False))
        self.assertFalse(self.avisos("Marca fora do padrão").exists())

    def test_sem_marca_continua_conforme(self):
        self.cadastrar_com(marca_padrao="")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.marca, insumo.insumo_global_marca, insumo.nao_conforme), ("", None, False))

    def test_personalizar_marca_vai_para_fora_do_padrao_e_avisa(self):
        self.cadastrar_com(marca_padrao="personalizar", marca="Marca da Vovó")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.insumo_global, insumo.insumo_global_marca, insumo.marca, insumo.nao_conforme), (self.acucar, None, "Marca da Vovó", True))
        self.assertTrue(self.avisos("Marca fora do padrão").filter(usuario=self.admin).exists())
        self.assertContains(self.client.get(reverse("insumo")), "personalizado")
        self.logar(self.admin)
        fila = self.client.get(reverse("insumo-nao-conformes"))
        self.assertContains(fila, "marca personalizada")
        self.assertContains(fila, "Padronizar marca")

    def test_personalizar_exige_o_nome_da_marca(self):
        resposta = self.cadastrar_com(marca_padrao="personalizar", marca="")
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(cliente=self.a).exists())

    def test_personalizar_digitando_marca_que_ja_existe_usa_a_padronizada(self):
        self.cadastrar_com(marca_padrao="personalizar", marca="uniao")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.insumo_global_marca, insumo.marca, insumo.nao_conforme), (self.uniao, "União", False))

    def test_marca_de_outro_insumo_e_recusada(self):
        farinha = InsumoGlobal.objects.create(nome="Farinha")
        outra, _ = services.adicionar_marca(None, farinha, "Dona Benta")
        resposta = self.cadastrar_com(marca_padrao=outra.pk)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(cliente=self.a).exists())

    def test_marca_inativa_nao_e_aceita(self):
        self.uniao.ativo = False
        self.uniao.save()
        self.assertEqual(self.cadastrar_com(marca_padrao=self.uniao.pk).status_code, 200)

    def test_pagina_oferece_dropdown_com_as_marcas_do_insumo(self):
        self.logar(self.gestor)
        pagina = self.client.get(reverse("insumo-create")).content.decode()
        self.assertIn('id="id_marca_padrao"', pagina)
        mapa = json.loads(pagina.split('id="marcas-por-padrao" type="application/json">')[1].split("</script>")[0])
        self.assertEqual({m["nome"] for m in mapa[str(self.acucar.pk)]}, {"União", "Caravelas"})

    def test_insumo_personalizado_usa_marca_em_texto_livre_e_vai_para_a_fila(self):
        self.logar(self.gestor)
        self.cadastrar("Cúrcuma Defumada", marca="Qualquer")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.insumo_global, insumo.marca, insumo.nao_conforme), (None, "Qualquer", True))

    def test_edicao_mostra_a_marca_atual_no_dropdown(self):
        self.cadastrar_com(marca_padrao=self.uniao.pk)
        insumo = Insumo.objects.get(cliente=self.a)
        pagina = self.client.get(reverse("insumo-edit", args=[insumo.pk])).content.decode()
        self.assertIn(f'data-inicial="{self.uniao.pk}"', pagina)

    def test_editar_sem_mudar_a_marca_personalizada_nao_avisa_de_novo(self):
        self.cadastrar_com(marca_padrao="personalizar", marca="Marca da Vovó")
        insumo = Insumo.objects.get(cliente=self.a)
        DestinatarioNotificacao.objects.all().delete()
        self.client.post(reverse("insumo-edit", args=[insumo.pk]), {"insumo_padrao": self.acucar.pk, "marca_padrao": "personalizar", "marca": "Marca da Vovó", "medida": insumo.medida_id, "quantidade": "1", "preco_pago": "12,00", "ativo": "on"})
        insumo.refresh_from_db()
        self.assertTrue(insumo.nao_conforme)
        self.assertFalse(DestinatarioNotificacao.objects.exists())


class FilaDeMarcaTests(Base):
    def setUp(self):
        super().setUp()
        self.uniao, _ = services.adicionar_marca(None, self.acucar, "União")
        self.insumo = Insumo.objects.create(cliente=self.a, nome="Açúcar", marca="Uniao Premium", insumo_global=self.acucar, nao_conforme=True,
                                            medida=self.acucar.medida, quantidade=1, preco_pago=1000)
        self.logar(self.admin)

    def test_padronizar_marca_cria_a_variacao_e_torna_conforme(self):
        self.client.post(reverse("insumo-nao-conforme-promover-marca", args=[self.insumo.pk]))
        self.insumo.refresh_from_db()
        self.assertFalse(self.insumo.nao_conforme)
        self.assertEqual((self.insumo.marca, self.insumo.insumo_global_marca.insumo_global), ("Uniao Premium", self.acucar))
        self.assertEqual(self.acucar.variacoes.count(), 2)

    def test_redirecionar_marca_para_uma_existente(self):
        self.client.post(reverse("insumo-nao-conforme-redirecionar-marca", args=[self.insumo.pk]), {"destino": self.uniao.pk})
        self.insumo.refresh_from_db()
        self.assertEqual((self.insumo.insumo_global_marca, self.insumo.marca, self.insumo.nao_conforme), (self.uniao, "União", False))

    def test_redirecionar_para_marca_de_outro_insumo_e_recusado(self):
        farinha = InsumoGlobal.objects.create(nome="Farinha")
        outra, _ = services.adicionar_marca(None, farinha, "Dona Benta")
        self.client.post(reverse("insumo-nao-conforme-redirecionar-marca", args=[self.insumo.pk]), {"destino": outra.pk})
        self.insumo.refresh_from_db()
        self.assertTrue(self.insumo.nao_conforme)

    def test_so_quem_pode_padronizar(self):
        self.logar(self.gestor)
        self.assertEqual(self.client.post(reverse("insumo-nao-conforme-promover-marca", args=[self.insumo.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("insumo-nao-conforme-redirecionar-marca", args=[self.insumo.pk]), {"destino": self.uniao.pk}).status_code, 403)
