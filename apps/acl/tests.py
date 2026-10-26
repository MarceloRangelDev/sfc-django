from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.cliente.models import Cliente

SENHA = "SenhaForte123!"


class PerfisTests(TestCase):
    """Gestor e Operador sempre visíveis; Admin oculto e intocável para quem não é Admin."""

    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.admin = self.novo("adm", "Administrador")
        self.gestor = self.novo("gestor", "Gestor")
        # Um Gestor com permissão de gerenciar papéis (cenário de delegação indevida).
        grupo = Group.objects.get(name="Gestor")
        from django.contrib.auth.models import Permission

        grupo.permissions.add(*Permission.objects.filter(content_type__app_label="auth", codename__endswith="_group"))

    def novo(self, username, perfil):
        usuario = User.objects.create_user(username=username, email=f"{username}@teste.local", password=SENHA)
        usuario.profile.cliente = self.cliente
        usuario.profile.save()
        usuario.groups.add(Group.objects.get(name=perfil))
        return usuario

    def nomes_da_lista(self, quem):
        self.client.login(username=quem, password=SENHA)
        return {g.name for g in self.client.get(reverse("acl-papel-list")).context["papeis"]}

    def test_gestor_e_operador_sempre_visiveis(self):
        nomes = self.nomes_da_lista("gestor")
        self.assertTrue({"Gestor", "Operador"} <= nomes)

    def test_admin_oculto_para_nao_admin(self):
        self.assertNotIn("Administrador", self.nomes_da_lista("gestor"))

    def test_admin_visivel_para_admin(self):
        self.assertIn("Administrador", self.nomes_da_lista("adm"))

    def test_nao_admin_nao_acessa_o_perfil_admin_por_url(self):
        admin_group = Group.objects.get(name="Administrador")
        self.client.login(username="gestor", password=SENHA)
        self.assertEqual(self.client.get(reverse("acl-papel-edit", args=[admin_group.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("acl-papel-edit", args=[admin_group.pk]), {"nome": "X"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("acl-papel-delete", args=[admin_group.pk])).status_code, 404)
        self.assertTrue(Group.objects.filter(name="Administrador").exists())

    def test_so_admin_altera_permissoes_do_admin(self):
        admin_group = Group.objects.get(name="Administrador")
        self.client.login(username="adm", password=SENHA)
        resposta = self.client.get(reverse("acl-papel-edit", args=[admin_group.pk]))
        self.assertEqual(resposta.status_code, 200)

    def test_perfil_admin_nao_pode_ser_renomeado_nem_excluido(self):
        admin_group = Group.objects.get(name="Administrador")
        self.client.login(username="adm", password=SENHA)
        self.client.post(reverse("acl-papel-edit", args=[admin_group.pk]), {"nome": "Outro Nome"})
        self.client.post(reverse("acl-papel-delete", args=[admin_group.pk]))
        self.assertTrue(Group.objects.filter(name="Administrador").exists())

    def test_api_de_perfis_tambem_oculta_o_admin(self):
        token, _ = Token.objects.get_or_create(user=self.gestor)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        corpo = api.get("/api/v1/perfis/").json()
        dados = corpo["data"]
        itens = dados["itens"] if isinstance(dados, dict) else dados
        nomes = {p["name"] for p in itens}
        self.assertIn("Gestor", nomes)
        self.assertNotIn("Administrador", nomes)
        admin_group = Group.objects.get(name="Administrador")
        self.assertEqual(api.get(f"/api/v1/perfis/{admin_group.pk}/").status_code, 404)
        self.assertEqual(api.delete(f"/api/v1/perfis/{admin_group.pk}/").status_code, 404)

    def test_matriz_de_permissoes_nunca_aceita_permissao_de_papeis(self):
        from django.contrib.auth.models import Permission

        proibida = Permission.objects.get(content_type__app_label="auth", codename="delete_group")
        self.client.login(username="adm", password=SENHA)
        self.client.post(reverse("acl-papel-create"), {"nome": "Teste", "permissoes": [proibida.pk]})
        self.assertFalse(Group.objects.get(name="Teste").permissions.filter(pk=proibida.pk).exists())


class OutrasPermissoesRecolhidasTests(TestCase):
    """"Outras permissões" começa recolhido, mas continua no formulário: salvar sem abrir o grupo não perde nenhuma marcação."""

    setUp = PerfisTests.setUp  # mesmo cenário (Administrador + Gestor), sem reexecutar os testes de PerfisTests
    novo = PerfisTests.novo

    def marcadas(self, html):
        import re

        return sorted(int(m) for m in re.findall(r'name="permissoes" value="(\d+)"[^>]*\bchecked\b', html))

    def test_grupo_vem_recolhido_com_interruptor_acessivel(self):
        self.client.login(username="adm", password=SENHA)
        html = self.client.get(reverse("acl-papel-edit", args=[Group.objects.get(name="Gestor").pk])).content.decode()
        self.assertIn('id="outras-permissoes" class="d-none"', html)
        self.assertIn('id="mostrar_outras"', html)
        self.assertIn('aria-controls="outras-permissoes"', html)
        self.assertIn('for="mostrar_outras"', html)  # rótulo associado ao checkbox

    def test_salvar_com_o_grupo_recolhido_preserva_as_permissoes(self):
        self.client.login(username="adm", password=SENHA)
        gestor = Group.objects.get(name="Gestor")
        html = self.client.get(reverse("acl-papel-edit", args=[gestor.pk])).content.decode()
        marcadas = set(self.marcadas(html))
        self.assertTrue(marcadas)
        # O navegador envia as caixas marcadas mesmo dentro do bloco escondido (d-none não remove os campos).
        self.client.post(reverse("acl-papel-edit", args=[gestor.pk]), {"nome": "Gestor", "permissoes": sorted(marcadas)})
        # Tudo o que a tela mostrava marcado (matriz + outras permissões, visíveis ou não) continua, e nada a mais aparece.
        self.assertEqual(set(Group.objects.get(name="Gestor").permissions.values_list("pk", flat=True)), marcadas)


class TelaDoPdvImplicaLeituraTests(TestCase):
    """Papel só com "Visualizar" da tela Comandas: o menu mostra e a tela abre (antes: item no menu e 403)."""

    def setUp(self):
        from apps.cliente.models import Cliente
        from web_project.testing import habilitar_modulo

        self.cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="A")
        habilitar_modulo(self.cliente, "pdv")
        grupo = Group.objects.create(name="operador-caixa")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="pdv", codename="view_telacomandas"))
        self.user = User.objects.create_user(username="oc", email="oc@t.local", password="SenhaForte123!")
        self.user.profile.cliente = self.cliente
        self.user.profile.save()
        self.user.groups.add(grupo)
        self.client.force_login(self.user)

    def test_tela_abre_e_aparece_no_menu(self):
        from web_project.menu import montar_menu

        self.assertEqual(self.client.get(reverse("pdv-comandas")).status_code, 200)
        self.assertEqual([s["slug"] for i in montar_menu(self.user) for s in i.get("submenu", [])], ["pdv-comandas"])

    def test_leitura_implicita_nao_concede_criar_e_negar_prevalece(self):
        from .models import PermissaoUsuario

        self.assertFalse(self.user.has_perm("pdv.add_venda"))
        PermissaoUsuario.objects.create(
            usuario=self.user, permissao=Permission.objects.get(content_type__app_label="pdv", codename="view_venda"), efeito=PermissaoUsuario.EFEITO_NEGAR
        )
        user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user.has_perm("pdv.view_venda"))
