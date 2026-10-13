from django.contrib.auth.models import Group, User
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
