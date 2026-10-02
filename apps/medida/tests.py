from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from web_project.regras import conversao

from .models import Medida

ORDEM_ESPERADA = [
    "Gramas", "Quilo", "Unidade", "Mililitro", "Litro", "Colher de chá", "Colher de sopa",
    "Colher de café", "Copo americano", "Xícara", "Polegadas", "Centímetro", "Metro",
    "Metro quadrado", "Metro Cúbico", "Segundos", "Quilômetro",
]


def criar_usuario(username, codenames=()):
    usuario = User.objects.create_user(username=username, email=f"{username}@teste.local", password="SenhaForte123!")
    if codenames:
        grupo = Group.objects.create(name=f"grupo-{username}")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="medida", codename__in=codenames))
        usuario.groups.add(grupo)
    return usuario


class CargaInicialTests(TestCase):
    def test_17_medidas_na_ordem_pedida(self):
        self.assertEqual(list(Medida.objects.values_list("medida", flat=True)), ORDEM_ESPERADA)
        self.assertEqual(list(Medida.objects.values_list("id", flat=True)), sorted(Medida.objects.values_list("id", flat=True)))

    def test_carga_e_idempotente(self):
        from importlib import import_module

        migracao = import_module("apps.medida.migrations.0002_carga_inicial_medidas")
        from django.apps import apps

        migracao.carregar_medidas(apps, None)
        self.assertEqual(Medida.objects.count(), 17)

    def test_carga_nao_sobrescreve_ajuste_do_administrador(self):
        from importlib import import_module

        from django.apps import apps

        Medida.objects.filter(medida="Xícara").update(fator_base=Decimal("250"))
        import_module("apps.medida.migrations.0002_carga_inicial_medidas").carregar_medidas(apps, None)
        self.assertEqual(Medida.objects.get(medida="Xícara").fator_base, Decimal("250"))

    def test_conversoes_usam_a_tabela(self):
        g, kg = Medida.objects.get(sigla="g"), Medida.objects.get(sigla="kg")
        ml, litro = Medida.objects.get(sigla="ml"), Medida.objects.get(sigla="L")
        self.assertEqual(conversao.converter(1000, g, kg), Decimal("1"))
        self.assertEqual(conversao.converter(500, ml, litro), Decimal("0.5"))
        self.assertEqual(conversao.preco_por_unidade_padrao(400, 100, g), 4000)
        self.assertEqual(conversao.preco_por_unidade_padrao(1000, 500, ml), 2000)

    def test_por_extenso(self):
        self.assertEqual(Medida.objects.get(sigla="g").por_extenso(100), "100 gramas")
        self.assertEqual(Medida.objects.get(sigla="kg").por_extenso(1), "1 quilo")
        self.assertEqual(Medida.objects.get(sigla="L").por_extenso(2), "2 litros")
        self.assertEqual(Medida.objects.get(sigla="c. sopa").por_extenso(2), "2 colheres de sopa")

    def test_nao_converte_dimensoes_diferentes(self):
        with self.assertRaises(conversao.RegraNegocioError):
            conversao.converter(1, Medida.objects.get(sigla="kg"), Medida.objects.get(sigla="L"))


class MedidaAcessoTests(TestCase):
    def test_anonimo_vai_para_login(self):
        resposta = self.client.get(reverse("medida-list"))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/login", resposta["Location"])

    def test_usuario_sem_permissao_recebe_403(self):
        criar_usuario("comum")
        self.client.login(username="comum", password="SenhaForte123!")
        for nome in ("medida-list", "medida-create"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403)
        self.assertEqual(self.client.post(reverse("medida-excluir", args=[1])).status_code, 403)
        self.assertEqual(self.client.post(reverse("medida-alternar-ativo", args=[1])).status_code, 403)

    def test_so_visualizar_nao_edita(self):
        criar_usuario("leitor", ["view_medida"])
        self.client.login(username="leitor", password="SenhaForte123!")
        self.assertEqual(self.client.get(reverse("medida-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("medida-edit", args=[1])).status_code, 403)


class MedidaCrudTests(TestCase):
    def setUp(self):
        self.admin = criar_usuario("admin", ["view_medida", "add_medida", "change_medida", "delete_medida"])
        self.client.login(username="admin", password="SenhaForte123!")

    def _dados(self, **extra):
        dados = {
            "medida": "Pitada", "sigla": "pit", "descricao": "", "equivale": "", "dimensao": "massa",
            "fator_base": "0,5", "nome_singular": "pitada", "nome_plural": "pitadas", "ativo": "on",
        }
        dados.update(extra)
        return dados

    def test_listagem_pesquisa_e_filtros(self):
        resposta = self.client.get(reverse("medida-list"), {"q": "colher"})
        self.assertEqual({m.sigla for m in resposta.context["medidas"]}, {"c. chá", "c. sopa", "c. café"})
        resposta = self.client.get(reverse("medida-list"), {"dimensao": "massa"})
        self.assertEqual({m.sigla for m in resposta.context["medidas"]}, {"g", "kg"})
        Medida.objects.filter(sigla="g").update(ativo=False)
        resposta = self.client.get(reverse("medida-list"), {"status": "inativo"})
        self.assertEqual([m.sigla for m in resposta.context["medidas"]], ["g"])

    def test_cadastro_grava_usuario_e_audita(self):
        resposta = self.client.post(reverse("medida-create"), self._dados())
        self.assertRedirects(resposta, reverse("medida-list"))
        medida = Medida.objects.get(sigla="pit")
        self.assertEqual(medida.usuario, self.admin)
        self.assertEqual(medida.fator_base, Decimal("0.5"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="medida", acao="criar", registro_id=str(medida.pk)).exists())

    def test_duplicidade_de_nome_e_sigla(self):
        self.assertContains(self.client.post(reverse("medida-create"), self._dados(medida="litro")), "Já existe uma medida com este nome")
        self.assertContains(self.client.post(reverse("medida-create"), self._dados(sigla="KG")), "Já existe uma medida com esta sigla")

    def test_fator_invalido(self):
        for fator in ["0", "-1", "abc", ""]:
            resposta = self.client.post(reverse("medida-create"), self._dados(fator_base=fator))
            self.assertEqual(resposta.status_code, 200, fator)
            self.assertTrue(resposta.context["form"].errors.get("fator_base"), fator)

    def test_edicao(self):
        medida = Medida.objects.get(sigla="g")
        dados = self._dados(medida="Gramas", sigla="g", dimensao="massa", fator_base="1", descricao="nova")
        self.assertRedirects(self.client.post(reverse("medida-edit", args=[medida.pk]), dados), reverse("medida-list"))
        medida.refresh_from_db()
        self.assertEqual(medida.descricao, "nova")

    def test_alternar_ativo(self):
        medida = Medida.objects.get(sigla="g")
        self.client.post(reverse("medida-alternar-ativo", args=[medida.pk]))
        medida.refresh_from_db()
        self.assertFalse(medida.ativo)
        self.client.post(reverse("medida-alternar-ativo", args=[medida.pk]))
        medida.refresh_from_db()
        self.assertTrue(medida.ativo)

    def test_exclusao_fisica_de_medida_sem_uso(self):
        self.client.post(reverse("medida-create"), self._dados())
        medida = Medida.objects.get(sigla="pit")
        self.client.post(reverse("medida-excluir", args=[medida.pk]))
        self.assertFalse(Medida.objects.filter(pk=medida.pk).exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="medida", acao="excluir", descricao__startswith="Excluído").exists())

    def test_exclusao_exige_post(self):
        self.assertEqual(self.client.get(reverse("medida-excluir", args=[1])).status_code, 405)
        self.assertTrue(Medida.objects.filter(pk=1).exists())


class CargaInicialComandoTests(TestCase):
    def test_seed_acl_da_permissoes_de_medida_so_ao_administrador(self):
        call_command("seed_acl", verbosity=0)
        admin = Group.objects.get(name="Administrador")
        self.assertEqual(admin.permissions.filter(content_type__app_label="medida").count(), 4)
        self.assertEqual(Group.objects.get(name="Gestor").permissions.filter(content_type__app_label="medida").count(), 0)
