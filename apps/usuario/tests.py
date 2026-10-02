from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from auth.models import Profile

SENHA_ADMIN = "SenhaForte123!"
SENHA_NOVA = "Outra#Senha2026x"


def dados_usuario(cliente, perfil, **extra):
    dados = {
        "username": "maria", "first_name": "Maria", "last_name": "Silva", "email": "maria@teste.local",
        "is_active": "on", "cliente": cliente.pk, "perfil": perfil.pk, "segmento_trabalho": "produto",
        "senha": SENHA_NOVA, "confirmar_senha": SENHA_NOVA,
    }
    dados.update(extra)
    return dados


class BaseUsuarioTests(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.outro_cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        self.operador = Group.objects.get(name="Operador")
        self.gestor = Group.objects.get(name="Gestor")
        self.cliente.perfis.set([self.operador, self.gestor])
        self.outro_cliente.perfis.set([self.gestor])  # Operador NÃO está liberado para o cliente B

        self.admin = User.objects.create_user(username="admin", email="admin@teste.local", password=SENHA_ADMIN)
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        self.client.login(username="admin", password=SENHA_ADMIN)

    def criar(self, **extra):
        return self.client.post(reverse("usuario-create"), dados_usuario(self.cliente, self.operador, **extra))


class CadastroTests(BaseUsuarioTests):
    def test_cria_usuario_com_cliente_perfil_e_senha_com_hash(self):
        resposta = self.criar()
        self.assertRedirects(resposta, reverse("usuario-list"))
        usuario = User.objects.get(username="maria")
        self.assertEqual(usuario.profile.cliente, self.cliente)
        self.assertEqual(usuario.profile.email, "maria@teste.local")
        self.assertEqual(list(usuario.groups.all()), [self.operador])
        self.assertNotEqual(usuario.password, SENHA_NOVA)
        self.assertTrue(usuario.password.startswith("pbkdf2_"))
        self.assertTrue(usuario.check_password(SENHA_NOVA))
        self.assertTrue(usuario.is_active)

    def test_o_novo_usuario_consegue_logar_e_acessa_so_o_que_o_perfil_permite(self):
        self.criar()
        self.client.logout()
        self.assertTrue(self.client.login(username="maria", password=SENHA_NOVA))
        self.assertEqual(self.client.get(reverse("usuario-list")).status_code, 403)

    def test_perfil_que_nao_pertence_ao_cliente_e_rejeitado(self):
        resposta = self.client.post(
            reverse("usuario-create"), dados_usuario(self.outro_cliente, self.operador)
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("perfil", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_email_duplicado_e_rejeitado_sem_diferenciar_maiusculas(self):
        self.criar()
        resposta = self.criar(username="maria2", email="MARIA@teste.local")
        self.assertIn("email", resposta.context["form"].errors)
        self.assertEqual(User.objects.filter(username="maria2").count(), 0)

    def test_email_do_profile_ja_usado_tambem_conta(self):
        Profile.objects.filter(user=self.admin).update(email="usado@teste.local")
        self.assertIn("email", self.criar(email="usado@teste.local").context["form"].errors)

    def test_username_duplicado_sem_diferenciar_maiusculas(self):
        self.criar()
        self.assertIn("username", self.criar(username="MARIA", email="outra@teste.local").context["form"].errors)

    def test_email_invalido(self):
        for email in ["abc", "a@@b.com", ""]:
            self.assertIn("email", self.criar(email=email).context["form"].errors, email)

    def test_senha_obrigatoria_na_criacao(self):
        self.assertIn("senha", self.criar(senha="", confirmar_senha="").context["form"].errors)

    def test_senha_fraca_e_rejeitada(self):
        for fraca in ["12345678", "password", "abc"]:
            self.assertIn("senha", self.criar(senha=fraca, confirmar_senha=fraca).context["form"].errors, fraca)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_confirmacao_diferente_e_rejeitada(self):
        self.assertIn("confirmar_senha", self.criar(confirmar_senha="Diferente#123x").context["form"].errors)

    def test_cliente_inexistente_ou_inativo_e_rejeitado(self):
        self.outro_cliente.ativo = False
        self.outro_cliente.save()
        resposta = self.client.post(reverse("usuario-create"), dados_usuario(self.outro_cliente, self.gestor))
        self.assertIn("cliente", resposta.context["form"].errors)

    def test_campos_de_privilegio_nao_sao_aceitos(self):
        self.criar(is_superuser="on", is_staff="on")
        usuario = User.objects.get(username="maria")
        self.assertFalse(usuario.is_superuser)
        self.assertFalse(usuario.is_staff)

    def test_criacao_e_atomica_se_a_auditoria_falhar(self):
        self.client.raise_request_exception = False
        with mock.patch("apps.usuario.services.registrar_criacao", side_effect=RuntimeError("falha")):
            resposta = self.criar()
        self.assertEqual(resposta.status_code, 500)
        self.assertFalse(User.objects.filter(username="maria").exists())
        self.assertFalse(Profile.objects.filter(email="maria@teste.local").exists())


class AuditoriaUsuarioTests(BaseUsuarioTests):
    def test_criacao_e_auditada_sem_expor_senha(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        registro = RegistroAuditoria.objects.get(acao="criar", modulo="usuario")
        self.assertEqual(registro.descricao, f"Criado usuário ID {usuario.pk} - maria.")
        self.assertEqual(registro.tabela, "auth_user")
        self.assertNotIn(usuario.password, registro.descricao)

    def test_edicao_lista_so_o_que_mudou_e_nunca_o_hash(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        self.client.post(reverse("usuario-edit", args=[usuario.pk]), dados_usuario(
            self.cliente, self.gestor, first_name="Mariana", senha=SENHA_NOVA + "9", confirmar_senha=SENHA_NOVA + "9",
        ))
        registro = RegistroAuditoria.objects.get(acao="editar", modulo="usuario")
        por_campo = {a["campo"]: a for a in registro.alteracoes}
        self.assertEqual(set(por_campo), {"first_name", "perfil", "password"})
        self.assertEqual((por_campo["perfil"]["antes"], por_campo["perfil"]["depois"]), ("Operador", "Gestor"))
        self.assertEqual(por_campo["password"]["depois"], "alterada")
        usuario.refresh_from_db()
        self.assertNotIn(usuario.password, str(registro.alteracoes) + registro.descricao)

    def test_editar_sem_mudar_nada_nao_gera_auditoria(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        self.client.post(reverse("usuario-edit", args=[usuario.pk]), dados_usuario(self.cliente, self.operador, senha="", confirmar_senha=""))
        self.assertFalse(RegistroAuditoria.objects.filter(acao="editar", modulo="usuario").exists())

    def test_senha_em_branco_mantem_a_senha(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        self.client.post(reverse("usuario-edit", args=[usuario.pk]), dados_usuario(self.cliente, self.operador, first_name="X", senha="", confirmar_senha=""))
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password(SENHA_NOVA))

    def test_inativar_e_auditado(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        self.client.post(reverse("usuario-alternar-ativo", args=[usuario.pk]))
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)
        alteracao = RegistroAuditoria.objects.get(acao="editar", modulo="usuario").alteracoes[0]
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("Sim", "Não"))
        self.client.logout()
        self.assertFalse(self.client.login(username="maria", password=SENHA_NOVA))  # inativo não loga


class RegrasDeSegurancaTests(BaseUsuarioTests):
    def test_nao_pode_inativar_a_propria_conta(self):
        self.client.post(reverse("usuario-alternar-ativo", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        resposta = self.client.post(
            reverse("usuario-edit", args=[self.admin.pk]),
            dados_usuario(self.cliente, self.operador, username="admin", email="admin@teste.local", is_active="", senha="", confirmar_senha=""),
        )
        self.assertIn("is_active", resposta.context["form"].errors)

    def test_nao_ha_exclusao_fisica(self):
        self.criar()
        usuario = User.objects.get(username="maria")
        self.assertEqual(self.client.delete(reverse("usuario-edit", args=[usuario.pk])).status_code, 405)
        self.assertTrue(User.objects.filter(pk=usuario.pk).exists())

    def test_usuario_comum_nao_acessa(self):
        self.criar()
        self.client.logout()
        self.client.login(username="maria", password=SENHA_NOVA)
        for nome, args in [("usuario-list", []), ("usuario-create", []), ("usuario-edit", [self.admin.pk])]:
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 403)
        self.assertEqual(self.client.post(reverse("usuario-alternar-ativo", args=[self.admin.pk])).status_code, 403)

    def test_anonimo_vai_para_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("usuario-list")).status_code, 302)

    def test_superusuario_so_e_visivel_e_editavel_por_superusuario(self):
        root = User.objects.create_superuser("root", "root@teste.local", SENHA_ADMIN)
        self.assertNotIn(root, list(self.client.get(reverse("usuario-list")).context["usuarios"]))
        self.assertEqual(self.client.get(reverse("usuario-edit", args=[root.pk])).status_code, 404)

    def _usuario_de_cliente_com_permissoes(self):
        """Gerente do cliente A com permissão de gerir usuários (mas NÃO administrador global)."""
        grupo = Group.objects.create(name="Gerente de usuários")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="auth", content_type__model="user"))
        self.cliente.perfis.add(grupo)
        gerente = User.objects.create_user(username="gerente", email="gerente@teste.local", password=SENHA_ADMIN)
        gerente.groups.add(grupo)
        gerente.profile.cliente = self.cliente
        gerente.profile.save()
        return gerente

    def test_gerente_de_cliente_so_ve_usuarios_do_proprio_cliente(self):
        gerente = self._usuario_de_cliente_com_permissoes()
        self.criar()
        self.client.post(reverse("usuario-create"), dados_usuario(self.outro_cliente, self.gestor, username="joao", email="joao@teste.local"))
        self.client.logout()
        self.client.login(username="gerente", password=SENHA_ADMIN)
        nomes = {u.username for u in self.client.get(reverse("usuario-list")).context["usuarios"]}
        self.assertEqual(nomes, {"gerente", "maria"})
        joao = User.objects.get(username="joao")
        self.assertEqual(self.client.get(reverse("usuario-edit", args=[joao.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("usuario-alternar-ativo", args=[joao.pk])).status_code, 404)
        joao.refresh_from_db()
        self.assertTrue(joao.is_active)
        self.assertEqual(gerente.profile.cliente, self.cliente)

    def test_gerente_nao_cria_usuario_em_outro_cliente(self):
        self._usuario_de_cliente_com_permissoes()
        self.client.logout()
        self.client.login(username="gerente", password=SENHA_ADMIN)
        resposta = self.client.post(reverse("usuario-create"), dados_usuario(self.outro_cliente, self.gestor))
        self.assertIn("cliente", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_listagem_filtra_e_pagina(self):
        self.criar()
        url = reverse("usuario-list")
        self.assertEqual([u.username for u in self.client.get(url, {"q": "mari"}).context["usuarios"]], ["maria"])
        self.assertEqual([u.username for u in self.client.get(url, {"perfil": self.operador.pk}).context["usuarios"]], ["maria"])
        self.assertEqual([u.username for u in self.client.get(url, {"status": "inativo"}).context["usuarios"]], [])
        self.assertEqual(self.client.get(url, {"cliente": "x", "perfil": "y", "per_page": "-1"}).status_code, 200)


class PerfisDoClienteTests(BaseUsuarioTests):
    def test_alteracao_dos_perfis_do_cliente_e_auditada(self):
        resposta = self.client.post(reverse("cliente-edit", args=[self.cliente.pk]), {
            "tipo_pessoa": "PJ", "cpf_cnpj": "11222333000181", "nome_razao_social": "Empresa A",
            "nome_fantasia": "", "ativo": "on", "perfis": [self.operador.pk],
        })
        self.assertEqual(resposta.status_code, 302)
        alteracao = RegistroAuditoria.objects.get(acao="editar", modulo="cliente").alteracoes[0]
        self.assertEqual(alteracao["rotulo"], "Perfis disponíveis")
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("Gestor, Operador", "Operador"))
        self.assertEqual(list(self.cliente.perfis.all()), [self.operador])

    def test_clientes_existentes_recebem_os_perfis_na_migracao_de_dados(self):
        from importlib import import_module

        from django.apps import apps

        novo = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="52998224725", nome_razao_social="PF")
        import_module("apps.cliente.migrations.0004_perfis_dos_clientes_existentes").liberar_perfis_existentes(apps, None)
        self.assertEqual(novo.perfis.count(), Group.objects.count())
