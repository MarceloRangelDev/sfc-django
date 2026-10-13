"""Habilitar um módulo para o cliente precisa torná-lo EFETIVO para os usuários dele.

Cenário relatado: módulo habilitado em "Módulos do cliente", mas o usuário do cliente
(perfil Gestor) não o enxergava, porque o perfil não tinha as permissões do módulo
(só chegavam pelo seed_acl); a "permissão especial" por usuário contornava.
"""
from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.cliente.services import criar_cliente
from apps.modulo.models import ClienteModulo, Modulo, Segmento

from . import apps as acl_apps
from .padroes import aplicar_padrao_aos_modulos, aplicar_padrao_do_modulo, permissoes_do_modulo

PERFIS = ("Administrador", "Gestor", "Operador", "Visualizador")


def perms_do_modulo(slug):
    return permissoes_do_modulo(Modulo.objects.get(slug=slug))


def tira_modulo_dos_perfis(slug):
    """Simula o banco antigo: perfis criados antes do módulo existir."""
    for grupo in Group.objects.filter(name__in=PERFIS):
        grupo.permissions.remove(*perms_do_modulo(slug))


class CenarioBase(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa", segmento=Segmento.objects.get(slug="produto"),
        )
        self.gestor = self.novo_usuario("gestor", "Gestor")
        self.operador = self.novo_usuario("operador", "Operador")
        self.visualizador = self.novo_usuario("visualizador", "Visualizador")
        self.admin = User.objects.create_user(username="adm", email="adm@t.local", password="SenhaForte123!", is_superuser=True)
        self.admin.profile.cliente = self.cliente
        self.admin.profile.save()

    def novo_usuario(self, nome, perfil):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
        u.profile.cliente = self.cliente
        u.profile.save()
        u.groups.add(Group.objects.get(name=perfil))
        return u

    def habilitar_pela_tela(self, *slugs):
        """O que o administrador faz em "Módulos do cliente": marca os módulos e salva."""
        ids = list(Modulo.objects.filter(slug__in=slugs).values_list("pk", flat=True))
        self.client.force_login(self.admin)
        resposta = self.client.post(reverse("modulo-list"), {"cliente_id": self.cliente.pk, "modulos": ids})
        self.assertEqual(resposta.status_code, 302)
        self.client.logout()

    def acessa(self, usuario, rota):
        self.client.force_login(User.objects.get(pk=usuario.pk))  # usuário "novo": sem cache de permissões
        codigo = self.client.get(reverse(rota)).status_code
        self.client.logout()
        return codigo


class HabilitarModuloTornaEfetivoTests(CenarioBase):
    def test_modulo_habilitado_mas_perfil_sem_permissao_so_funciona_apos_habilitar_pela_tela(self):
        tira_modulo_dos_perfis("estoque")
        ClienteModulo.objects.update_or_create(cliente=self.cliente, modulo=Modulo.objects.get(slug="estoque"), defaults={"ativo": True})
        # Estado do relato: habilitado para o cliente, mas o usuário não entra.
        self.assertEqual(self.acessa(self.gestor, "estoque"), 403)
        self.habilitar_pela_tela("estoque")  # o admin salva a tela de módulos do cliente
        self.assertEqual(self.acessa(self.gestor, "estoque"), 200)
        self.assertEqual(self.acessa(self.operador, "estoque"), 200)
        self.assertEqual(self.acessa(self.visualizador, "estoque"), 200)

    def test_cada_perfil_recebe_so_o_seu_padrao(self):
        tira_modulo_dos_perfis("estoque")
        self.habilitar_pela_tela("estoque")
        tem = lambda perfil, cod: Group.objects.get(name=perfil).permissions.filter(codename=cod).exists()
        self.assertTrue(tem("Gestor", "change_movimentoestoque") and not tem("Gestor", "delete_movimentoestoque"))
        self.assertTrue(tem("Operador", "add_movimentoestoque") and not tem("Operador", "change_movimentoestoque"))
        self.assertTrue(tem("Visualizador", "view_movimentoestoque") and not tem("Visualizador", "add_movimentoestoque"))

    def test_habilitar_outro_modulo_nao_acrescenta_o_que_nao_foi_habilitado(self):
        tira_modulo_dos_perfis("estoque")
        tira_modulo_dos_perfis("custo-operacional")
        self.habilitar_pela_tela("custo-operacional")
        self.assertEqual(self.acessa(self.gestor, "custo-list"), 200)
        self.assertEqual(self.acessa(self.gestor, "estoque"), 403)  # estoque segue sem permissão e sem habilitação

    def test_modulo_desabilitado_continua_bloqueado_mesmo_com_permissao(self):
        self.habilitar_pela_tela("insumo")  # só insumo: o estoque fica desabilitado
        self.assertEqual(self.acessa(self.gestor, "estoque"), 403)

    def test_perfil_ja_configurado_para_o_modulo_nao_e_alterado(self):
        tira_modulo_dos_perfis("estoque")
        operador = Group.objects.get(name="Operador")
        operador.permissions.add(Permission.objects.get(codename="view_movimentoestoque"))  # ajuste manual: só consulta
        self.habilitar_pela_tela("estoque")
        self.assertFalse(operador.permissions.filter(codename="add_movimentoestoque").exists())  # respeitou o ajuste
        self.assertTrue(Group.objects.get(name="Gestor").permissions.filter(codename="add_movimentoestoque").exists())

    def test_perfis_nao_padrao_nunca_sao_alterados(self):
        outro = Group.objects.create(name="Perfil do cliente")
        self.habilitar_pela_tela("estoque", "financeiro-pagar")
        self.assertEqual(outro.permissions.count(), 0)

    def test_idempotente_e_so_acrescenta(self):
        tira_modulo_dos_perfis("estoque")
        self.habilitar_pela_tela("estoque")
        antes = {g.name: set(g.permissions.values_list("pk", flat=True)) for g in Group.objects.filter(name__in=PERFIS)}
        self.habilitar_pela_tela("estoque")
        self.assertEqual(aplicar_padrao_aos_modulos(), {})  # nada a completar
        depois = {g.name: set(g.permissions.values_list("pk", flat=True)) for g in Group.objects.filter(name__in=PERFIS)}
        self.assertEqual(antes, depois)

    def test_permissao_sensivel_so_para_administrador_e_gestor(self):
        tira_modulo_dos_perfis("estoque-transferencia")
        tira_modulo_dos_perfis("financeiro-relatorios")
        aplicar_padrao_aos_modulos()
        tem = lambda perfil, cod: Group.objects.get(name=perfil).permissions.filter(codename=cod).exists()
        for cod in ("view_transferenciaestoque", "view_relatoriofinanceiro"):
            self.assertTrue(tem("Gestor", cod) and tem("Administrador", cod), cod)
            self.assertFalse(tem("Operador", cod) or tem("Visualizador", cod), cod)

    def test_desabilitar_nao_remove_permissoes(self):
        self.habilitar_pela_tela("estoque")
        quantidade = Group.objects.get(name="Gestor").permissions.count()
        self.habilitar_pela_tela("insumo")  # desliga o estoque
        self.assertEqual(Group.objects.get(name="Gestor").permissions.count(), quantidade)


class OutrosCaminhosTests(CenarioBase):
    def test_cliente_novo_ja_nasce_com_modulos_efetivos(self):
        for slug in ("estoque", "financeiro-pagar"):
            tira_modulo_dos_perfis(slug)
        novo = Cliente(tipo_pessoa="PJ", cpf_cnpj="34028316000103", nome_razao_social="Nova", segmento=Segmento.objects.get(slug="produto"))
        pedido = RequestFactory().post("/")
        pedido.user = self.admin
        criar_cliente(pedido, novo, [], "gestor@nova.local")
        usuario = User.objects.get(profile__cliente=novo)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("estoque")).status_code, 200)
        self.assertEqual(self.client.get(reverse("conta-pagar-list")).status_code, 200)

    def test_post_migrate_completa_modulos_novos(self):
        tira_modulo_dos_perfis("estoque")
        ClienteModulo.objects.update_or_create(cliente=self.cliente, modulo=Modulo.objects.get(slug="estoque"), defaults={"ativo": True})
        ultimo = [c for c in apps.get_app_configs() if c.models_module is not None][-1]
        primeiro = apps.get_app_config("auth")
        acl_apps._completar_permissoes_padrao(primeiro)  # emissão de outro app: não faz nada
        self.assertEqual(self.acessa(self.gestor, "estoque"), 403)
        acl_apps._completar_permissoes_padrao(ultimo)  # fim do migrate
        self.assertEqual(self.acessa(self.gestor, "estoque"), 200)

    def test_banco_sem_perfis_nao_quebra(self):
        Group.objects.filter(name__in=PERFIS).delete()
        self.assertEqual(aplicar_padrao_aos_modulos(), {})

    def test_modulo_sem_permissao_cadastrada_e_ignorado(self):
        modulo = Modulo.objects.create(nome="Sem perm", slug="sem-perm", permissao="")
        self.assertEqual(aplicar_padrao_do_modulo(modulo), {})
        modulo2 = Modulo.objects.create(nome="Perm inexistente", slug="perm-x", permissao="app_que_nao_existe.view_coisa")
        self.assertEqual(aplicar_padrao_do_modulo(modulo2), {})

    def test_seed_acl_continua_redefinindo_os_perfis_para_o_padrao(self):
        Group.objects.get(name="Operador").permissions.clear()
        call_command("seed_acl", verbosity=0)
        self.assertTrue(Group.objects.get(name="Operador").permissions.filter(codename="add_contapagar").exists())
