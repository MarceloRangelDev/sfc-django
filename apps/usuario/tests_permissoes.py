from django.contrib.auth.models import Group, Permission, User
from django.urls import reverse

from apps.acl.models import PermissaoUsuario
from apps.auditoria.models import RegistroAuditoria
from web_project.testing import habilitar_modulo

from .tests import SENHA_ADMIN, SENHA_NOVA, BaseUsuarioTests, dados_usuario


def perm(codename, app):
    return Permission.objects.get(codename=codename, content_type__app_label=app)


def campo(codename, app="insumo"):
    return f"perm_{perm(codename, app).pk}"


class PermissoesIndividuaisTests(BaseUsuarioTests):
    def setUp(self):
        super().setUp()
        habilitar_modulo(self.cliente, "insumo")
        self.criar(username="maria", email="maria@teste.local")
        self.maria = User.objects.get(username="maria")

    def editar(self, usuario=None, cliente=None, **extra):
        usuario = usuario or self.maria
        dados = dados_usuario(cliente or self.cliente, self.operador, username=usuario.username, email=usuario.email, **extra)
        return self.client.post(reverse("usuario-edit", args=[usuario.pk]), dados)

    def recarregar(self, usuario):
        return User.objects.get(pk=usuario.pk)  # has_perm guarda cache no objeto

    def test_formulario_mostra_a_matriz_de_permissoes(self):
        for nome in ("usuario-create",):
            resposta = self.client.get(reverse(nome))
            self.assertContains(resposta, f'name="{campo("view_insumo")}"')
        resposta = self.client.get(reverse("usuario-edit", args=[self.maria.pk]))
        self.assertContains(resposta, f'name="{campo("delete_insumo")}"')

    def test_sem_excecoes_o_usuario_so_herda_o_papel(self):
        maria = self.recarregar(self.maria)
        self.assertTrue(maria.has_perm("insumo.view_insumo"))
        self.assertFalse(maria.has_perm("insumo.delete_insumo"))
        self.assertFalse(PermissaoUsuario.objects.exists())

    def test_permitir_e_negar_prevalecem_sobre_o_papel(self):
        resposta = self.editar(**{campo("delete_insumo"): "permitir", campo("view_insumo"): "negar"})
        self.assertRedirects(resposta, reverse("usuario-list"))
        maria = self.recarregar(self.maria)
        self.assertTrue(maria.has_perm("insumo.delete_insumo"))  # o papel não concede
        self.assertFalse(maria.has_perm("insumo.view_insumo"))   # o papel concede, a exceção nega
        self.assertTrue(maria.has_perm("insumo.add_insumo"))     # sem exceção: herda

    def test_servidor_aplica_as_excecoes_nas_rotas(self):
        self.client.logout()
        self.client.login(username="maria", password=SENHA_NOVA)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 200)
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[999])).status_code, 403)  # papel não deixa excluir
        self.client.logout()
        self.client.login(username="admin", password=SENHA_ADMIN)
        self.editar(**{campo("delete_insumo"): "permitir", campo("view_insumo"): "negar"})
        self.client.logout()
        self.client.login(username="maria", password=SENHA_NOVA)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[999])).status_code, 404)  # passou da permissão

    def test_herdar_remove_a_excecao(self):
        self.editar(**{campo("view_insumo"): "negar"})
        self.assertEqual(PermissaoUsuario.objects.filter(usuario=self.maria).count(), 1)
        self.editar(**{campo("view_insumo"): ""})
        self.assertFalse(PermissaoUsuario.objects.filter(usuario=self.maria).exists())
        self.assertTrue(self.recarregar(self.maria).has_perm("insumo.view_insumo"))

    def test_editar_um_usuario_nao_altera_outros_nem_outro_tenant(self):
        self.criar(username="joao", email="joao@teste.local")
        joao = User.objects.get(username="joao")
        self.client.post(
            reverse("usuario-create"),
            dados_usuario(self.outro_cliente, self.gestor, username="ana", email="ana@teste.local"),
        )
        ana = User.objects.get(username="ana")
        self.editar(**{campo("view_insumo"): "negar"})
        self.assertEqual(set(PermissaoUsuario.objects.values_list("usuario__username", flat=True)), {"maria"})
        self.assertTrue(self.recarregar(joao).has_perm("insumo.view_insumo"))
        self.assertTrue(self.recarregar(ana).has_perm("insumo.view_insumo"))

    def test_post_sem_campos_de_permissao_nao_altera_as_excecoes(self):
        self.editar(**{campo("view_insumo"): "negar"})
        self.editar()  # nenhum campo perm_* (ex.: API)
        self.assertEqual(PermissaoUsuario.objects.get(usuario=self.maria).efeito, "negar")

    def test_ids_e_valores_invalidos_sao_rejeitados(self):
        indevida = perm("delete_group", "auth")  # fora da matriz de módulos delegáveis
        resposta = self.editar(**{f"perm_{indevida.pk}": "permitir"})
        self.assertEqual(resposta.status_code, 200)
        resposta = self.editar(**{campo("view_insumo"): "talvez"})
        self.assertEqual(resposta.status_code, 200)
        resposta = self.editar(**{"perm_abc": "negar"})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(PermissaoUsuario.objects.exists())
        self.assertFalse(self.recarregar(self.maria).has_perm("auth.delete_group"))

    def test_nao_concede_o_que_o_solicitante_nao_possui(self):
        rh = Group.objects.create(name="RH")
        rh.permissions.set(Permission.objects.filter(
            content_type__app_label="auth", content_type__model="user", codename__in=["view_user", "add_user", "change_user"],
        ))
        self.cliente.perfis.add(rh)
        chefe = User.objects.create_user(username="chefe", email="chefe@teste.local", password=SENHA_NOVA)
        chefe.profile.cliente = self.cliente
        chefe.profile.save()
        chefe.groups.add(rh)
        self.client.logout()
        self.client.login(username="chefe", password=SENHA_NOVA)
        resposta = self.editar(**{campo("delete_insumo"): "permitir"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não possui")
        self.assertFalse(PermissaoUsuario.objects.exists())
        # negar nunca amplia acesso: permitido
        self.assertRedirects(self.editar(**{campo("delete_insumo"): "negar"}), reverse("usuario-list"))
        self.assertEqual(PermissaoUsuario.objects.get(usuario=self.maria).efeito, "negar")

    def test_superusuario_nao_perde_acesso_por_excecao(self):
        raiz = User.objects.create_superuser(username="raiz", email="raiz@teste.local", password=SENHA_ADMIN)
        PermissaoUsuario.objects.create(usuario=raiz, permissao=perm("view_insumo", "insumo"), efeito="negar")
        self.assertTrue(self.recarregar(raiz).has_perm("insumo.view_insumo"))

    def test_alteracao_e_auditada(self):
        self.editar(**{campo("view_insumo"): "negar"})
        registro = RegistroAuditoria.objects.filter(descricao__contains="Permissões individuais").first()
        self.assertIsNotNone(registro)
        self.assertIn("Negar: insumo.view_insumo", registro.descricao)

    def test_criacao_ja_com_excecoes(self):
        self.criar(username="nova", email="nova@teste.local", **{campo("delete_insumo"): "permitir"})
        nova = User.objects.get(username="nova")
        self.assertTrue(self.recarregar(nova).has_perm("insumo.delete_insumo"))
