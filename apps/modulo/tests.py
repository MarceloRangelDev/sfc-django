from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from web_project.menu import montar_menu
from web_project.testing import habilitar_modulo

from .models import ClienteModulo, Modulo, Segmento


def criar_usuario_com_cliente(username, cliente, permissoes=("view_insumo",)):
    usuario = User.objects.create_user(username=username, email=f"{username}@teste.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    grupo, _ = Group.objects.get_or_create(name=f"Grupo {username}")
    grupo.permissions.set(Permission.objects.filter(content_type__app_label="insumo", codename__in=permissoes))
    usuario.groups.add(grupo)
    return usuario


class ModuloGateTests(TestCase):
    """O módulo precisa ser bloqueado no BACKEND (acesso direto à URL),
    não só escondido do menu — ver web_project/decorators.py::module_required.
    Regra: segmento do cliente + habilitação do cliente + permissão do perfil."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa Teste",
            segmento=Segmento.objects.get(slug="produto"),
        )
        self.modulo_insumo = Modulo.objects.get(slug="insumo")
        self.usuario = criar_usuario_com_cliente("usuario_modulo", self.cliente)
        self.client.login(username="usuario_modulo", password="SenhaForte123!")

    def test_acesso_negado_quando_modulo_nao_habilitado_para_o_cliente(self):
        # O segmento tem o módulo, mas nenhum ClienteModulo foi criado: não está habilitado.
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

    def test_acesso_permitido_quando_modulo_habilitado(self):
        habilitar_modulo(self.cliente, "insumo")
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 200)

    def test_acesso_negado_quando_modulo_desabilitado_explicitamente(self):
        habilitar_modulo(self.cliente, "insumo", ativo=False)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

    def test_acesso_negado_quando_modulo_esta_globalmente_indisponivel(self):
        habilitar_modulo(self.cliente, "insumo")
        self.modulo_insumo.ativo = False
        self.modulo_insumo.save()
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

    def test_reabilitar_modulo_restaura_o_acesso(self):
        vinculo = habilitar_modulo(self.cliente, "insumo", ativo=False)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)
        vinculo.ativo = True
        vinculo.save()
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 200)

    def test_modulo_fora_do_segmento_do_cliente_e_negado_mesmo_habilitado(self):
        habilitar_modulo(self.cliente, "insumo")
        self.cliente.segmento.modulos.remove(self.modulo_insumo)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

    def test_cliente_sem_segmento_nao_acessa_modulos_de_negocio(self):
        habilitar_modulo(self.cliente, "insumo")
        Cliente.objects.filter(pk=self.cliente.pk).update(segmento=None)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

    def test_usuario_sem_permissao_nao_acessa_a_url_direto_mesmo_com_modulo_habilitado(self):
        habilitar_modulo(self.cliente, "insumo")
        Group.objects.get(name="Grupo usuario_modulo").permissions.clear()
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)


class MenuDinamicoTests(TestCase):
    """O menu vem da tabela de módulos e só mostra o que o usuário pode usar."""

    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa Menu",
            segmento=Segmento.objects.get(slug="produto"),
        )
        self.usuario = criar_usuario_com_cliente("usuario_menu", self.cliente)

    def nomes(self, itens):
        return [i["name"] for i in itens]

    def test_modulo_habilitado_aparece_com_o_icone_do_banco(self):
        habilitar_modulo(self.cliente, "insumo")
        Modulo.objects.filter(slug="insumo").update(icone="ti tabler-egg")
        item = next(i for i in montar_menu(self.usuario) if i["name"] == "Insumos")
        self.assertIn("tabler-egg", item["icon"])
        self.assertEqual(item["url"], "insumo")

    def test_modulo_desabilitado_nao_aparece(self):
        habilitar_modulo(self.cliente, "insumo", ativo=False)
        self.assertNotIn("Insumos", self.nomes(montar_menu(self.usuario)))

    def test_modulo_fora_do_segmento_nao_aparece(self):
        habilitar_modulo(self.cliente, "insumo")
        self.cliente.segmento.modulos.remove(Modulo.objects.get(slug="insumo"))
        self.assertNotIn("Insumos", self.nomes(montar_menu(self.usuario)))

    def test_modulo_sem_permissao_do_perfil_nao_aparece(self):
        habilitar_modulo(self.cliente, "insumo")
        Group.objects.get(name="Grupo usuario_menu").permissions.clear()
        self.assertNotIn("Insumos", self.nomes(montar_menu(User.objects.get(pk=self.usuario.pk))))

    def test_configuracoes_so_mostra_submenus_com_permissao(self):
        # O usuário só tem permissão de Insumos: nenhum submenu de Configurações.
        self.assertNotIn("Configurações", self.nomes(montar_menu(self.usuario)))
        self.usuario.groups.add(Group.objects.get(name="Gestor"))
        menu = montar_menu(User.objects.get(pk=self.usuario.pk))
        configuracoes = next(i for i in menu if i["name"] == "Configurações")
        nomes = self.nomes(configuracoes["submenu"])
        self.assertIn("Usuários", nomes)
        self.assertNotIn("Papéis", nomes)  # exclusivo do Administrador

    def test_rota_invalida_nao_derruba_o_menu(self):
        habilitar_modulo(self.cliente, "insumo")
        Modulo.objects.filter(slug="insumo").update(rota="rota-que-nao-existe")
        self.assertNotIn("Insumos", self.nomes(montar_menu(self.usuario)))


class ModuloClienteTelaTests(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa A",
            segmento=Segmento.objects.get(slug="produto"),
        )
        self.outra = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11444777000161", nome_razao_social="Empresa B",
            segmento=Segmento.objects.get(slug="produto"),
        )
        self.admin = User.objects.create_user(username="admin_t", email="admin_t@teste.local", password="SenhaForte123!")
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        self.client.login(username="admin_t", password="SenhaForte123!")

    def test_salvar_habilita_so_os_modulos_marcados_do_segmento(self):
        insumo = Modulo.objects.get(slug="insumo")
        resposta = self.client.post(reverse("modulo-list"), {"cliente_id": self.cliente.pk, "modulos": [insumo.pk]})
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(ClienteModulo.objects.get(cliente=self.cliente, modulo=insumo).ativo)
        self.assertFalse(ClienteModulo.objects.get(cliente=self.cliente, modulo__slug="produto").ativo)

    def test_usuario_com_permissao_nao_altera_cliente_de_outra_estrutura(self):
        gestor = User.objects.create_user(username="gestor_t", email="gestor_t@teste.local", password="SenhaForte123!")
        gestor.profile.cliente = self.cliente
        gestor.profile.save()
        grupo = Group.objects.create(name="Config Modulos")
        grupo.permissions.add(Permission.objects.get(codename="change_clientemodulo"))
        gestor.groups.add(grupo)
        self.client.login(username="gestor_t", password="SenhaForte123!")
        resposta = self.client.post(reverse("modulo-list"), {"cliente_id": self.outra.pk, "modulos": []})
        self.assertEqual(resposta.status_code, 404)

    def test_novo_modulo_aparece_na_matriz_de_papeis(self):
        from apps.acl.permissions_matrix import montar_matriz_permissoes

        self.assertNotIn("Relatórios", [linha["nome"] for linha in montar_matriz_permissoes()])
        Modulo.objects.create(nome="Relatórios", slug="relatorios", permissao="insumo.view_insumo")
        self.assertIn("Relatórios", [linha["nome"] for linha in montar_matriz_permissoes()])

    def test_modulo_nao_delegavel_fica_fora_da_matriz(self):
        from apps.acl.permissions_matrix import montar_matriz_permissoes

        nomes = [linha["nome"] for linha in montar_matriz_permissoes()]
        self.assertNotIn("Papéis", nomes)
        self.assertNotIn("Auditoria", nomes)


class ColunaTipoTests(TestCase):
    """A listagem de módulos mostra o TIPO conforme o campo `essencial`."""

    def test_catalogo_exibe_tipo_por_segmento_e_essencial(self):
        admin = User.objects.create_superuser(username="root", email="root@teste.local", password="SenhaForte123!")
        self.client.force_login(admin)
        resposta = self.client.get(reverse("modulo-catalogo"))
        self.assertContains(resposta, "<th>Tipo</th>", html=True)
        linhas = {m.slug: m for m in resposta.context["modulos"]}
        self.assertTrue(linhas["cliente"].essencial)
        self.assertFalse(linhas["insumo"].essencial)
        html = resposta.content.decode()
        linha_cliente = html[html.index("<code>cliente</code>"):].split("</tr>")[0]
        linha_insumo = html[html.index("<code>insumo</code>"):].split("</tr>")[0]
        self.assertIn("Essencial", linha_cliente)
        self.assertIn("Por segmento", linha_insumo)


class MenuAtivoTests(TestCase):
    """Em qualquer tela de um módulo de Configurações o submenu fica aberto e o item marcado."""

    def setUp(self):
        call_command("seed_acl", verbosity=0)
        call_command("seed_modulos", verbosity=0)
        self.admin = User.objects.create_superuser(username="root", email="root@teste.local", password="SenhaForte123!")
        self.client.force_login(self.admin)

    def menu(self, url):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        inicio = html.index('<ul class="menu-inner')
        return html[inicio:html.index("</aside>", inicio)]

    def item_ativo(self, menu):
        import re
        return re.findall(r'<li class="menu-item active">\s*<a href="([^"]+)"', menu)

    def test_lista_marca_o_item_e_abre_configuracoes(self):
        menu = self.menu(reverse("segmento-list"))
        self.assertIn('menu-item active open', menu)
        self.assertEqual(self.item_ativo(menu), [reverse("segmento-list")])

    def test_telas_internas_mantem_o_menu_do_modulo(self):
        for url in (reverse("segmento-create"), reverse("modulo-create"), reverse("usuario-create"), reverse("cliente-create")):
            menu = self.menu(url)
            self.assertIn('menu-item active open', menu, url)
            self.assertEqual(len(self.item_ativo(menu)), 1, url)

    def test_modulos_com_nomes_parecidos_nao_se_confundem(self):
        # /modulos (Módulos do cliente) e /modulos/catalogo (Módulos) são itens distintos.
        self.assertEqual(self.item_ativo(self.menu(reverse("modulo-list"))), [reverse("modulo-list")])
        self.assertEqual(self.item_ativo(self.menu(reverse("modulo-catalogo"))), [reverse("modulo-catalogo")])

    def test_pagina_fora_de_configuracoes_nao_abre_o_submenu(self):
        menu = self.menu(reverse("insumo"))
        self.assertNotIn("menu-item active open", menu)
        self.assertEqual(self.item_ativo(menu), [reverse("insumo")])
