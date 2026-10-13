"""Matriz/Filial, isolamento entre estruturas, Gestor padrão e regras do Admin."""
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.modulo.models import Segmento
from web_project.acesso import clientes_acessiveis, usuarios_acessiveis

from .models import Cliente

SENHA = "SenhaForte123!"
CNPJS = ["11222333000181", "11444777000161", "04252011000110", "45997418000153", "33000167000101"]


def novo_cliente(nome, cnpj, **extra):
    return Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        segmento=Segmento.objects.get(slug="produto"), **extra,
    )


def novo_usuario(username, cliente, perfil=None):
    usuario = User.objects.create_user(username=username, email=f"{username}@teste.local", password=SENHA)
    usuario.profile.cliente = cliente
    usuario.profile.save()
    if perfil:
        usuario.groups.add(Group.objects.get(name=perfil))
    return usuario


class EstruturaBase(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        # Estrutura A (matriz + 2 filiais) e estrutura B (matriz + 1 filial).
        self.matriz_a = novo_cliente("Matriz A", CNPJS[0])
        self.filial_a1 = novo_cliente("Filial A1", CNPJS[1], tipo_estabelecimento="filial", matriz=self.matriz_a)
        self.filial_a2 = novo_cliente("Filial A2", CNPJS[2], tipo_estabelecimento="filial", matriz=self.matriz_a)
        self.matriz_b = novo_cliente("Matriz B", CNPJS[3])
        self.filial_b1 = novo_cliente("Filial B1", CNPJS[4], tipo_estabelecimento="filial", matriz=self.matriz_b)


class RegrasMatrizFilialTests(EstruturaBase):
    def validar(self, **campos):
        base = dict(tipo_pessoa="PJ", cpf_cnpj="07526557000100", nome_razao_social="X")
        base.update(campos)
        Cliente(**base).full_clean()

    def test_filial_exige_matriz(self):
        with self.assertRaises(ValidationError):
            self.validar(tipo_estabelecimento="filial")

    def test_somente_cnpj_pode_ser_filial(self):
        with self.assertRaises(ValidationError):
            self.validar(tipo_pessoa="PF", cpf_cnpj="52998224725", tipo_estabelecimento="filial", matriz=self.matriz_a)

    def test_matriz_da_filial_precisa_ser_matriz(self):
        with self.assertRaises(ValidationError):
            self.validar(tipo_estabelecimento="filial", matriz=self.filial_a1)

    def test_matriz_nao_pode_ter_matriz(self):
        with self.assertRaises(ValidationError):
            self.validar(tipo_estabelecimento="matriz", matriz=self.matriz_a)

    def test_matriz_com_filiais_nao_vira_filial(self):
        self.matriz_a.tipo_estabelecimento, self.matriz_a.matriz = "filial", self.matriz_b
        with self.assertRaises(ValidationError):
            self.matriz_a.full_clean()

    def test_filial_valida_e_aceita(self):
        self.filial_a1.full_clean()


class EscopoDeClientesTests(EstruturaBase):
    def nomes(self, usuario):
        return set(clientes_acessiveis(usuario).values_list("nome_razao_social", flat=True))

    def test_matriz_acessa_suas_filiais_e_nada_de_outra_matriz(self):
        usuario = novo_usuario("u_matriz_a", self.matriz_a)
        self.assertEqual(self.nomes(usuario), {"Matriz A", "Filial A1", "Filial A2"})

    def test_filial_acessa_sua_matriz_e_as_irmas_mas_nao_outra_estrutura(self):
        usuario = novo_usuario("u_filial_a1", self.filial_a1)
        self.assertEqual(self.nomes(usuario), {"Matriz A", "Filial A1", "Filial A2"})
        self.assertNotIn("Matriz B", self.nomes(usuario))

    def test_usuarios_visiveis_sao_so_da_mesma_estrutura(self):
        u_a = novo_usuario("ua", self.filial_a1)
        novo_usuario("ua2", self.matriz_a)
        novo_usuario("ub", self.filial_b1)
        self.assertEqual(set(usuarios_acessiveis(u_a).values_list("username", flat=True)), {"ua", "ua2"})

    def test_admin_tem_acesso_global(self):
        admin = novo_usuario("adm", self.matriz_a, "Administrador")
        self.assertEqual(clientes_acessiveis(admin).count(), 5)

    def test_usuario_sem_cliente_nao_ve_nenhum(self):
        usuario = User.objects.create_user(username="solto", email="solto@teste.local", password=SENHA)
        self.assertEqual(clientes_acessiveis(usuario).count(), 0)


class IsolamentoHttpTests(EstruturaBase):
    def setUp(self):
        super().setUp()
        self.gestor_a = novo_usuario("gestor_a", self.matriz_a, "Gestor")
        Group.objects.get(name="Gestor").permissions.add(
            *Permission.objects.filter(content_type__app_label="cliente", codename__in=["change_cliente"])
        )
        self.client.login(username="gestor_a", password=SENHA)

    def test_lista_mostra_so_a_propria_estrutura(self):
        corpo = self.client.get(reverse("cliente-list")).content.decode()
        self.assertIn("Filial A2", corpo)
        self.assertNotIn("Matriz B", corpo)
        self.assertNotIn("Filial B1", corpo)

    def test_editar_cliente_de_outra_estrutura_por_url_da_404(self):
        self.assertEqual(self.client.get(reverse("cliente-edit", args=[self.matriz_b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("cliente-edit", args=[self.matriz_b.pk]), {}).status_code, 404)

    def test_editar_filial_da_propria_estrutura_funciona(self):
        self.assertEqual(self.client.get(reverse("cliente-edit", args=[self.filial_a2.pk])).status_code, 200)

    def test_nao_vincula_usuario_a_cliente_so_o_admin_faz(self):
        solto = User.objects.create_user(username="solto", email="solto@teste.local", password=SENHA)
        resposta = self.client.post(reverse("cliente-vincular-usuario", args=[self.matriz_a.pk]), {"user_id": solto.pk})
        self.assertEqual(resposta.status_code, 302)
        solto.profile.refresh_from_db()
        self.assertIsNone(solto.profile.cliente)

    def test_usuario_de_outra_estrutura_nao_aparece_nem_edita(self):
        ub = novo_usuario("ub", self.filial_b1, "Operador")
        self.assertNotIn("ub@teste.local", self.client.get(reverse("usuario-list")).content.decode())
        self.assertEqual(self.client.get(reverse("usuario-edit", args=[ub.pk])).status_code, 404)

    def test_api_nao_acessa_cliente_de_outra_estrutura(self):
        token, _ = Token.objects.get_or_create(user=self.gestor_a)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        self.assertEqual(api.get(f"/api/v1/clientes/{self.matriz_a.pk}/").status_code, 200)
        self.assertEqual(api.get(f"/api/v1/clientes/{self.matriz_b.pk}/").status_code, 404)
        ids = [c["id"] for c in api.get("/api/v1/clientes/?ativo=todos").json()["data"]["itens"]]
        self.assertNotIn(self.matriz_b.pk, ids)
        self.assertEqual(api.get(f"/api/v1/clientes/{self.matriz_b.pk}/modulos/").status_code, 404)


class GestorPadraoTests(EstruturaBase):
    def setUp(self):
        super().setUp()
        self.admin = novo_usuario("adm", self.matriz_a, "Administrador")
        self.client.login(username="adm", password=SENHA)
        self.dados = {
            "tipo_pessoa": "PJ", "cpf_cnpj": "07526557000100", "nome_razao_social": "Cliente Novo Ltda",
            "nome_fantasia": "", "ativo": "on", "tipo_estabelecimento": "matriz",
            "segmento": Segmento.objects.get(slug="produto").pk, "email_gestor": "gestor.novo@teste.local",
        }

    def test_criar_cliente_cria_usuario_gestor_vinculado_com_o_mesmo_nome(self):
        resposta = self.client.post(reverse("cliente-create"), self.dados)
        self.assertRedirects(resposta, reverse("cliente-list"))
        cliente = Cliente.objects.get(cpf_cnpj="07526557000100")
        usuario = User.objects.get(username="Cliente Novo Ltda")
        self.assertEqual(usuario.profile.cliente, cliente)
        self.assertEqual(usuario.first_name, "Cliente Novo Ltda")
        self.assertEqual([g.name for g in usuario.groups.all()], ["Gestor"])
        self.assertFalse(usuario.has_usable_password())  # senha definida por "Esqueci minha senha"
        self.assertIn(Group.objects.get(name="Gestor"), cliente.perfis.all())
        self.assertTrue(usuario.has_perm("auth.add_user"))  # permissões padrão do Gestor
        self.assertEqual(RegistroAuditoria.objects.filter(acao="criar", modulo="usuario").count(), 1)

    def test_modulos_do_segmento_nascem_habilitados(self):
        self.client.post(reverse("cliente-create"), self.dados)
        cliente = Cliente.objects.get(cpf_cnpj="07526557000100")
        self.assertTrue(cliente.modulos_habilitados.filter(modulo__slug="insumo", ativo=True).exists())

    def test_falha_na_criacao_do_gestor_nao_deixa_cliente_orfao(self):
        Group.objects.filter(name="Gestor").delete()
        resposta = self.client.post(reverse("cliente-create"), self.dados)
        self.assertEqual(resposta.status_code, 200)  # formulário com erro
        self.assertFalse(Cliente.objects.filter(cpf_cnpj="07526557000100").exists())
        self.assertFalse(User.objects.filter(username="Cliente Novo Ltda").exists())

    def test_email_do_gestor_duplicado_e_rejeitado(self):
        self.dados["email_gestor"] = "adm@teste.local"
        self.client.post(reverse("cliente-create"), self.dados)
        self.assertFalse(Cliente.objects.filter(cpf_cnpj="07526557000100").exists())

    def test_username_repetido_recebe_o_documento(self):
        User.objects.create_user(username="Cliente Novo Ltda", email="outro@teste.local")
        self.client.post(reverse("cliente-create"), self.dados)
        self.assertTrue(User.objects.filter(username="Cliente Novo Ltda 07526557000100").exists())

    def test_gestor_de_uma_estrutura_so_cria_filial_da_propria_matriz(self):
        gestor = novo_usuario("gestor_a", self.matriz_a, "Gestor")
        Group.objects.get(name="Gestor").permissions.add(Permission.objects.get(codename="add_cliente"))
        self.client.login(username="gestor_a", password=SENHA)
        # Tentativa de criar como matriz independente: o tipo só aceita "filial".
        self.assertEqual(self.client.post(reverse("cliente-create"), self.dados).status_code, 200)
        self.assertFalse(Cliente.objects.filter(cpf_cnpj="07526557000100").exists())
        # Filial de OUTRA matriz: a matriz não está entre as opções.
        self.dados.update(tipo_estabelecimento="filial", matriz=self.matriz_b.pk)
        self.assertEqual(self.client.post(reverse("cliente-create"), self.dados).status_code, 200)
        self.assertFalse(Cliente.objects.filter(cpf_cnpj="07526557000100").exists())
        # Filial da própria matriz: herda o segmento dela.
        self.dados.update(matriz=self.matriz_a.pk)
        self.client.post(reverse("cliente-create"), self.dados)
        nova = Cliente.objects.get(cpf_cnpj="07526557000100")
        self.assertEqual((nova.matriz, nova.segmento), (self.matriz_a, self.matriz_a.segmento))
        self.assertIsNotNone(gestor.pk)


class AdminSomenteNoClienteDoSistemaTests(EstruturaBase):
    def setUp(self):
        super().setUp()
        self.sistema = novo_cliente("SISTEMA FATURANDO CERTO SERVIÇOS LTDA", "07526557000100")
        Cliente.objects.filter(pk=self.sistema.pk).update(sistema=True)
        self.sistema.refresh_from_db()
        grupos = Group.objects.filter(name__in=["Administrador", "Gestor", "Operador"])
        for cliente in (self.sistema, self.matriz_a):
            cliente.perfis.set(grupos)
        self.admin = novo_usuario("adm", self.sistema, "Administrador")
        self.client.login(username="adm", password=SENHA)

    def dados(self, cliente, perfil, **extra):
        base = {
            "username": "novo", "first_name": "N", "last_name": "U", "email": "novo@teste.local", "is_active": "on",
            "cliente": cliente.pk, "perfil": Group.objects.get(name=perfil).pk, "senha": "Outra#Senha2026x",
            "confirmar_senha": "Outra#Senha2026x",
        }
        base.update(extra)
        return base

    def test_admin_nao_pode_ser_vinculado_a_outro_cliente(self):
        resposta = self.client.post(reverse("usuario-create"), self.dados(self.matriz_a, "Administrador"))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(User.objects.filter(username="novo").exists())

    def test_admin_pode_ser_criado_no_cliente_do_sistema(self):
        self.client.post(reverse("usuario-create"), self.dados(self.sistema, "Administrador"))
        self.assertTrue(User.objects.get(username="novo").groups.filter(name="Administrador").exists())

    def test_transformar_usuario_em_admin_fora_do_sistema_e_barrado(self):
        usuario = novo_usuario("comum", self.matriz_a, "Gestor")
        resposta = self.client.post(
            reverse("usuario-edit", args=[usuario.pk]),
            self.dados(self.matriz_a, "Administrador", username="comum", email="comum@teste.local"),
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(usuario.groups.filter(name="Administrador").exists())

    def test_nao_admin_nao_ve_perfil_admin_nem_cria_admin(self):
        gestor = novo_usuario("gestor_a", self.matriz_a, "Gestor")
        self.assertIsNotNone(gestor.pk)
        self.client.login(username="gestor_a", password=SENHA)
        corpo = self.client.get(reverse("usuario-create")).content.decode()
        self.assertNotIn("Administrador", corpo)
        resposta = self.client.post(reverse("usuario-create"), self.dados(self.matriz_a, "Administrador"))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(User.objects.filter(username="novo").exists())

    def test_nao_admin_nao_edita_um_admin(self):
        novo_usuario("gestor_s", self.sistema, "Gestor")
        self.client.login(username="gestor_s", password=SENHA)
        # O Admin do cliente do sistema está na estrutura do Gestor do sistema, mas continua intocável.
        resposta = self.client.post(
            reverse("usuario-edit", args=[self.admin.pk]),
            self.dados(self.sistema, "Gestor", username="adm", email="adm@teste.local"),
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(self.admin.groups.filter(name="Administrador").exists())

    def test_vincular_admin_a_outro_cliente_pela_tela_de_clientes_e_barrado(self):
        solto = User.objects.create_user(username="admsolto", email="admsolto@teste.local", password=SENHA)
        solto.groups.add(Group.objects.get(name="Administrador"))
        self.client.post(reverse("cliente-vincular-usuario", args=[self.matriz_a.pk]), {"user_id": solto.pk})
        solto.profile.refresh_from_db()
        self.assertIsNone(solto.profile.cliente)
        self.client.post(reverse("cliente-vincular-usuario", args=[self.sistema.pk]), {"user_id": solto.pk})
        solto.profile.refresh_from_db()
        self.assertEqual(solto.profile.cliente, self.sistema)

    def test_api_tambem_barra_admin_fora_do_sistema(self):
        token, _ = Token.objects.get_or_create(user=self.admin)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        corpo = {
            "username": "novo", "email": "novo@teste.local", "cliente": self.matriz_a.pk,
            "perfil": Group.objects.get(name="Administrador").pk, "senha": "Outra#Senha2026x",
            "confirmar_senha": "Outra#Senha2026x",
        }
        self.assertEqual(api.post("/api/v1/usuarios/", corpo, format="json").status_code, 400)
        corpo["cliente"] = self.sistema.pk
        self.assertEqual(api.post("/api/v1/usuarios/", corpo, format="json").status_code, 201)
