"""Ordem personalizada do menu por cliente: persistência, efeito no menu, validação, escopo de tenant e permissões."""
import json

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from web_project.menu import montar_menu
from web_project.testing import habilitar_modulo

from .models import ClienteModulo, Modulo, OrdemMenuCliente, Segmento
from .services import modulos_ordenaveis

SLUGS = ("insumo", "produto", "ficha")


class OrdemDoMenuTests(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.segmento = Segmento.objects.get(slug="produto")
        self.cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa A", segmento=self.segmento)
        self.outra = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11444777000161", nome_razao_social="Empresa B", segmento=self.segmento)
        for c in (self.cliente, self.outra):
            for slug in SLUGS:
                habilitar_modulo(c, slug)
        self.admin = User.objects.create_user(username="adm", email="adm@t.local", password="SenhaForte123!")
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        self.gestor = User.objects.create_user(username="gest", email="g@t.local", password="SenhaForte123!")
        self.gestor.profile.cliente = self.cliente
        self.gestor.profile.save()
        self.gestor.groups.add(Group.objects.get(name="Gestor"))
        self.client.force_login(self.admin)

    def ordem_atual(self, cliente=None):
        return [m.pk for m in modulos_ordenaveis(cliente or self.cliente)]

    def enviar(self, corpo):
        return self.client.post(reverse("modulo-ordem"), json.dumps(corpo), content_type="application/json")

    def nomes_no_menu(self, usuario):
        return [i["name"] for i in montar_menu(usuario) if i["slug"] in SLUGS]

    def test_padrao_sem_linhas_personalizadas(self):
        self.assertFalse(OrdemMenuCliente.objects.exists())
        ids = self.ordem_atual()
        self.assertEqual({Modulo.objects.get(pk=i).slug for i in ids} & set(SLUGS), set(SLUGS))

    def test_salvar_persiste_e_muda_o_menu_so_deste_cliente(self):
        padrao = self.nomes_no_menu(self.gestor)
        ids = self.ordem_atual()
        r = self.enviar({"cliente_id": self.cliente.pk, "modulos": list(reversed(ids))})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.ordem_atual(), list(reversed(ids)))  # persistiu no banco
        novo = self.nomes_no_menu(self.gestor)
        self.assertEqual([n for n in novo if n in padrao], [n for n in reversed(padrao)])
        # outra empresa mantém o padrão
        outro_usuario = User.objects.create_user(username="gest_b", email="gb@t.local", password="SenhaForte123!")
        outro_usuario.profile.cliente = self.outra
        outro_usuario.profile.save()
        outro_usuario.groups.add(Group.objects.get(name="Gestor"))
        self.assertEqual(self.nomes_no_menu(outro_usuario), padrao)

    def test_voltar_ao_padrao_remove_as_linhas_e_restaurar_tambem(self):
        ids = self.ordem_atual()
        self.enviar({"cliente_id": self.cliente.pk, "modulos": list(reversed(ids))})
        self.assertTrue(OrdemMenuCliente.objects.filter(cliente=self.cliente).exists())
        self.enviar({"cliente_id": self.cliente.pk, "modulos": ids})  # mesma ordem padrão
        self.assertFalse(OrdemMenuCliente.objects.filter(cliente=self.cliente).exists())
        self.enviar({"cliente_id": self.cliente.pk, "modulos": list(reversed(ids))})
        r = self.enviar({"cliente_id": self.cliente.pk, "restaurar": True})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(OrdemMenuCliente.objects.exists())

    def test_lista_invalida_e_recusada_e_a_ordem_anterior_e_mantida(self):
        ids = self.ordem_atual()
        self.enviar({"cliente_id": self.cliente.pk, "modulos": list(reversed(ids))})
        antes = self.ordem_atual()
        invalidas = (ids[:-1], ids + [ids[0]], ids + [999999], ["x"], None, "abc")
        for lista in invalidas:
            r = self.enviar({"cliente_id": self.cliente.pk, "modulos": lista})
            self.assertEqual(r.status_code, 422, lista)
            self.assertIn("erro", r.json())
        self.assertEqual(self.ordem_atual(), antes)

    def test_modulo_nao_habilitado_do_cliente_nao_entra_na_ordem(self):
        habilitar_modulo(self.cliente, "ficha", ativo=False)
        ids = self.ordem_atual()
        ficha = Modulo.objects.get(slug="ficha").pk
        self.assertNotIn(ficha, ids)
        self.assertEqual(self.enviar({"cliente_id": self.cliente.pk, "modulos": ids + [ficha]}).status_code, 422)

    def test_cliente_de_outra_estrutura_e_404_e_sem_permissao_e_403(self):
        ids = self.ordem_atual()
        # gestor só alcança o próprio cliente; sem change_clientemodulo na matriz padrão => bloqueado pela permissão
        self.client.force_login(self.gestor)
        self.assertEqual(self.enviar({"cliente_id": self.cliente.pk, "modulos": ids}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.enviar({"cliente_id": 999999, "modulos": ids}).status_code, 404)
        self.assertEqual(self.client.get(reverse("modulo-ordem")).status_code, 405)
        self.assertEqual(self.client.post(reverse("modulo-ordem"), "nao-json", content_type="application/json").status_code, 422)

    def test_tela_mostra_a_lista_ordenavel_com_alternativa_por_botoes(self):
        r = self.client.get(reverse("modulo-list") + f"?cliente={self.cliente.pk}")
        self.assertContains(r, 'id="listaOrdem"')
        self.assertContains(r, 'data-mover="-1"')
        self.assertContains(r, 'data-mover="1"')

    def test_modulos_extras_do_menu_continuam_visiveis_apos_reordenar(self):
        ids = self.ordem_atual()
        self.enviar({"cliente_id": self.cliente.pk, "modulos": list(reversed(ids))})
        self.assertEqual(ClienteModulo.objects.filter(cliente=self.cliente, ativo=True).count(), len(SLUGS))
