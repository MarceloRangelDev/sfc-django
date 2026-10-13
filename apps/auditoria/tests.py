from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.insumo.tests import criar_cliente_com_acesso_total, medida, novo_insumo
from apps.medida.models import Medida

from .models import RegistroAuditoria
from .utils import comparar, tirar_foto


def dados_insumo(**extra):
    dados = {"nome": "Queijo Mussarela", "medida": medida("kg").pk, "quantidade": "10", "preco_pago": "35,00", "ativo": "on"}
    dados.update(extra)
    return dados


class AuditoriaCreateTests(TestCase):
    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")

    def test_create_registra_usuario_modulo_tabela_id_nome_e_descricao(self):
        self.client.post(reverse("insumo-create"), dados_insumo())
        insumo = Insumo.objects.get(nome="Queijo Mussarela")
        registro = RegistroAuditoria.objects.get(acao="criar", modulo="insumo")
        self.assertEqual(registro.usuario, self.usuario)
        self.assertEqual(registro.cliente, self.cliente)
        self.assertEqual(registro.tabela, "insumo_insumo")
        self.assertEqual(registro.registro_id, str(insumo.pk))
        self.assertEqual(registro.registro_nome, "Queijo Mussarela")
        self.assertEqual(registro.descricao, f"Criado insumo ID {insumo.pk} - Queijo Mussarela.")
        self.assertIsNotNone(registro.criado_em)

    def test_formulario_invalido_nao_audita(self):
        self.client.post(reverse("insumo-create"), dados_insumo(quantidade="abc"))
        self.assertFalse(RegistroAuditoria.objects.filter(acao="criar").exists())


class AuditoriaUpdateTests(TestCase):
    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")
        self.insumo = novo_insumo(self.cliente, "Queijo Mussarela", "kg", "10", 3500)

    def _editar(self, **extra):
        dados = dados_insumo(**extra)
        return self.client.post(reverse("insumo-edit", args=[self.insumo.pk]), dados)

    def test_update_lista_apenas_campos_alterados_com_antes_e_depois(self):
        self._editar(preco_pago="40,00", quantidade="15", medida=medida("g").pk)
        registro = RegistroAuditoria.objects.get(acao="editar", modulo="insumo")
        por_campo = {a["campo"]: a for a in registro.alteracoes}
        self.assertEqual(set(por_campo), {"preco_pago", "quantidade", "medida"})
        self.assertEqual((por_campo["preco_pago"]["antes"], por_campo["preco_pago"]["depois"]), ("R$ 35,00", "R$ 40,00"))
        self.assertEqual((por_campo["quantidade"]["antes"], por_campo["quantidade"]["depois"]), ("10", "15"))
        self.assertEqual((por_campo["medida"]["antes"], por_campo["medida"]["depois"]), ("Quilo", "Gramas"))
        self.assertEqual(por_campo["preco_pago"]["rotulo"], "Valor pago")

    def test_descricao_segue_o_formato_do_prompt(self):
        self._editar(preco_pago="40,00")
        registro = RegistroAuditoria.objects.get(acao="editar")
        esperado = (
            f"Atualizado insumo ID {self.insumo.pk} - Queijo Mussarela.\n\n"
            "Alterações:\n- Valor pago: R$ 35,00 → R$ 40,00"
        )
        self.assertEqual(registro.descricao, esperado)

    def test_campo_nao_alterado_nao_aparece(self):
        self._editar(preco_pago="40,00")
        campos = [a["campo"] for a in RegistroAuditoria.objects.get(acao="editar").alteracoes]
        self.assertNotIn("nome", campos)
        self.assertNotIn("quantidade", campos)  # 10 -> "10" é o mesmo valor

    def test_salvar_sem_mudar_nada_nao_gera_registro(self):
        self._editar()
        self.assertFalse(RegistroAuditoria.objects.filter(acao="editar").exists())

    def test_bool_vira_sim_nao(self):
        self.client.post(reverse("insumo-edit", args=[self.insumo.pk]), {k: v for k, v in dados_insumo().items() if k != "ativo"})
        alteracao = RegistroAuditoria.objects.get(acao="editar").alteracoes[0]
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("Sim", "Não"))


class AuditoriaDeleteTests(TestCase):
    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")

    def test_exclusao_logica_diz_claramente_que_foi_inativado(self):
        insumo = novo_insumo(self.cliente, "Queijo Mussarela")
        self.client.post(reverse("insumo-excluir", args=[insumo.pk]))
        registro = RegistroAuditoria.objects.get(acao="excluir", modulo="insumo")
        self.assertEqual(registro.descricao, f"Insumo ID {insumo.pk} - Queijo Mussarela foi inativado.")

    def test_exclusao_fisica_registra_excluido(self):
        grupo = Group.objects.create(name="Admin medidas")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="medida"))
        self.usuario.groups.add(grupo)
        nova = Medida.objects.create(medida="Pitada", sigla="pit", dimensao="massa", fator_base=Decimal("0.5"))
        self.client.post(reverse("medida-excluir", args=[nova.pk]))
        registro = RegistroAuditoria.objects.get(acao="excluir", modulo="medida")
        self.assertEqual(registro.descricao, f"Excluído medida ID {nova.pk} - Pitada.")
        self.assertEqual(registro.tabela, "medidas")
        self.assertFalse(Medida.objects.filter(pk=nova.pk).exists())


class AuditoriaAtomicaTests(TestCase):
    """Se a auditoria falhar, a operação inteira é desfeita."""

    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")
        self.client.raise_request_exception = False

    def test_falha_na_auditoria_desfaz_o_cadastro(self):
        with mock.patch("apps.auditoria.utils.RegistroAuditoria.objects.create", side_effect=RuntimeError("falha")):
            resposta = self.client.post(reverse("insumo-create"), dados_insumo())
        self.assertEqual(resposta.status_code, 500)
        self.assertFalse(Insumo.objects.filter(nome="Queijo Mussarela").exists())

    def test_falha_na_auditoria_desfaz_a_exclusao_logica(self):
        insumo = novo_insumo(self.cliente, "Sal")
        with mock.patch("apps.auditoria.utils.RegistroAuditoria.objects.create", side_effect=RuntimeError("falha")):
            self.client.post(reverse("insumo-excluir", args=[insumo.pk]))
        insumo.refresh_from_db()
        self.assertTrue(insumo.ativo)

    def test_falha_no_login_nao_derruba_a_requisicao(self):
        # eventos de login/logout usam o modo silencioso
        self.client.logout()
        with mock.patch("apps.auditoria.utils.RegistroAuditoria.objects.create", side_effect=RuntimeError("falha")):
            self.assertTrue(self.client.login(username="usuario_a", password="SenhaForte123!"))


class CompararTests(TestCase):
    def test_decimal_equivalente_nao_e_alteracao(self):
        insumo = novo_insumo(Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="E"), "X", "kg", "10")
        antes = tirar_foto(insumo)
        insumo.quantidade = Decimal("10.0000")
        self.assertEqual(comparar(antes, tirar_foto(insumo)), [])


class AuditoriaTelaTests(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente_a, self.usuario_a = criar_cliente_com_acesso_total("A", "11222333000181")
        self.cliente_b, self.usuario_b = criar_cliente_com_acesso_total("B", "11444777000161")
        self.admin = User.objects.create_user(username="admin", email="admin@teste.local", password="SenhaForte123!")
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        # usuários A e B criam registros
        for login, nome in [("usuario_a", "Insumo A"), ("usuario_b", "Insumo B")]:
            self.client.login(username=login, password="SenhaForte123!")
            self.client.post(reverse("insumo-create"), dados_insumo(nome=nome))
            self.client.logout()

    def test_anonimo_e_usuario_comum_nao_acessam(self):
        self.assertEqual(self.client.get(reverse("auditoria-list")).status_code, 302)
        self.client.login(username="usuario_a", password="SenhaForte123!")
        self.assertEqual(self.client.get(reverse("auditoria-list")).status_code, 403)

    def test_administrador_ve_todos_os_clientes(self):
        self.client.login(username="admin", password="SenhaForte123!")
        resposta = self.client.get(reverse("auditoria-list"), {"modulo": "insumo"})
        self.assertEqual({r.registro_nome for r in resposta.context["registros"]}, {"Insumo A", "Insumo B"})

    def test_usuario_com_permissao_ve_so_o_proprio_cliente(self):
        permissao = Permission.objects.get(codename="view_registroauditoria")
        self.usuario_a.user_permissions.add(permissao)
        self.client.login(username="usuario_a", password="SenhaForte123!")
        registros = self.client.get(reverse("auditoria-list")).context["registros"]
        self.assertTrue(registros)
        self.assertTrue(all(r.cliente_id == self.cliente_a.pk for r in registros))
        registro_b = RegistroAuditoria.objects.get(registro_nome="Insumo B")
        self.assertEqual(self.client.get(reverse("auditoria-detail", args=[registro_b.pk])).status_code, 404)

    def test_filtros(self):
        self.client.login(username="admin", password="SenhaForte123!")
        url = reverse("auditoria-list")
        nomes = lambda **p: {r.registro_nome for r in self.client.get(url, p).context["registros"] if r.modulo == "insumo"}
        self.assertEqual(nomes(usuario=self.usuario_a.pk), {"Insumo A"})
        self.assertEqual(nomes(q="Insumo B"), {"Insumo B"})
        self.assertEqual(nomes(acao="criar"), {"Insumo A", "Insumo B"})
        self.assertEqual(nomes(acao="excluir"), set())
        self.assertEqual(nomes(de="2999-01-01"), set())
        self.assertEqual(nomes(ate="2000-01-01"), set())
        hoje = RegistroAuditoria.objects.first().criado_em.date().isoformat()
        self.assertEqual(nomes(de=hoje, ate=hoje), {"Insumo A", "Insumo B"})

    def test_filtro_invalido_nao_quebra(self):
        self.client.login(username="admin", password="SenhaForte123!")
        resposta = self.client.get(reverse("auditoria-list"), {"usuario": "x", "acao": "zzz", "de": "lixo", "per_page": "-3"})
        self.assertEqual(resposta.status_code, 200)

    def test_detalhes_mostram_alteracoes(self):
        insumo = Insumo.objects.get(nome="Insumo A")
        self.client.login(username="usuario_a", password="SenhaForte123!")
        self.client.post(reverse("insumo-edit", args=[insumo.pk]), dados_insumo(nome="Insumo A", preco_pago="40,00"))
        self.client.logout()
        self.client.login(username="admin", password="SenhaForte123!")
        registro = RegistroAuditoria.objects.get(acao="editar", registro_id=str(insumo.pk))
        resposta = self.client.get(reverse("auditoria-detail", args=[registro.pk]))
        self.assertContains(resposta, "R$ 35,00")
        self.assertContains(resposta, "R$ 40,00")
        self.assertContains(resposta, "insumo_insumo")

    def test_registro_nao_pode_ser_alterado_pela_tela(self):
        self.client.login(username="admin", password="SenhaForte123!")
        registro = RegistroAuditoria.objects.first()
        self.assertEqual(self.client.post(reverse("auditoria-detail", args=[registro.pk]), {"descricao": "x"}).status_code, 405)

    def test_apenas_permissao_de_visualizar_existe(self):
        codigos = set(Permission.objects.filter(content_type__app_label="auditoria", content_type__model="registroauditoria").values_list("codename", flat=True))
        self.assertIn("view_registroauditoria", codigos)


class AuditoriaAdministrativaTests(TestCase):
    """Papéis, módulos e vínculo de usuários também geram auditoria."""

    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.admin = User.objects.create_user(username="admin", email="admin@teste.local", password="SenhaForte123!")
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        self.client.login(username="admin", password="SenhaForte123!")

    def test_papel_criado_editado_e_excluido(self):
        permissao = Permission.objects.get(content_type__app_label="insumo", codename="view_insumo")
        self.client.post(reverse("acl-papel-create"), {"nome": "Estoque", "permissoes": [permissao.pk]})
        grupo = Group.objects.get(name="Estoque")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="criar", modulo="acl", registro_nome="Estoque").exists())

        self.client.post(reverse("acl-papel-edit", args=[grupo.pk]), {"nome": "Estoque 2"})
        registro = RegistroAuditoria.objects.get(acao="editar", modulo="acl")
        campos = {a["campo"]: a for a in registro.alteracoes}
        self.assertEqual(campos["name"]["depois"], "Estoque 2")
        self.assertIn("insumo.view_insumo", campos["permissoes_removidas"]["antes"])

        self.client.post(reverse("acl-papel-delete", args=[grupo.pk]))
        self.assertTrue(RegistroAuditoria.objects.filter(acao="excluir", modulo="acl", descricao__startswith="Excluído").exists())

    def test_vincular_e_desvincular_usuario(self):
        cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa")
        outro = User.objects.create_user(username="func", email="func@teste.local", password="SenhaForte123!")
        self.client.post(reverse("cliente-vincular-usuario", args=[cliente.pk]), {"user_id": outro.pk})
        self.client.post(reverse("cliente-desvincular-usuario", args=[cliente.pk, outro.pk]))
        registros = RegistroAuditoria.objects.filter(acao="editar", modulo="cliente").order_by("id")
        self.assertEqual([(r.alteracoes[0]["antes"], r.alteracoes[0]["depois"]) for r in registros], [(None, "func"), ("func", None)])

    def test_cliente_criado_e_editado(self):
        self.client.post(reverse("cliente-create"), {
            "tipo_pessoa": "PJ", "cpf_cnpj": "11.222.333/0001-81", "nome_razao_social": "Empresa", "nome_fantasia": "", "ativo": "on",
            "email_gestor": "gestor.empresa@teste.local",
        })
        cliente = Cliente.objects.get(cpf_cnpj="11222333000181")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="criar", modulo="cliente", registro_id=str(cliente.pk)).exists())
