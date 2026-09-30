from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.modulo.models import ClienteModulo, Modulo

from .models import Insumo


def criar_cliente_com_acesso_total(sufixo, cpf_cnpj):
    """Cria um Cliente com o módulo de Insumos habilitado e um usuário com
    todas as permissões de Insumo — pronto para testar o CRUD."""
    cliente = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cpf_cnpj, nome_razao_social=f"Empresa {sufixo}"
    )
    modulo, _ = Modulo.objects.get_or_create(slug="insumo", defaults={"nome": "Insumos", "ativo": True})
    ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})

    grupo, _ = Group.objects.get_or_create(name=f"Grupo Insumo {sufixo}")
    permissoes = Permission.objects.filter(content_type__app_label="insumo")
    grupo.permissions.set(permissoes)

    usuario = User.objects.create_user(
        username=f"usuario_{sufixo.lower()}",
        email=f"usuario_{sufixo.lower()}@teste.local",
        password="SenhaForte123!",
    )
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)

    categoria = Categoria.objects.create(cliente=cliente, nome="Categoria Padrão")

    return cliente, usuario, categoria


class InsumoValidacaoTests(TestCase):
    """Validação de valores — nunca confiar no que vem do formulário."""

    def setUp(self):
        self.cliente, self.usuario, self.categoria = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")

    def _payload(self, **overrides):
        dados = {
            "nome": "Farinha de Trigo",
            "categoria": self.categoria.pk,
            "unidade_medida": Insumo.UNIDADE_KG,
            "quantidade": "10",
            "preco": "12,90",
            "ativo": "on",
        }
        dados.update(overrides)
        return dados

    def test_preco_negativo_e_rejeitado(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(preco="-10"))
        self.assertEqual(resposta.status_code, 200)  # re-renderiza o form com erro
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_quantidade_nao_numerica_e_rejeitada(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(quantidade="abc"))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_preco_infinity_e_rejeitado(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(preco="Infinity"))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_quantidade_nan_e_rejeitada(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(quantidade="NaN"))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_valor_no_formato_brasileiro_e_aceito(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(preco="1.234,56"))
        self.assertRedirects(resposta, reverse("insumo"))
        insumo = Insumo.objects.get(nome="Farinha de Trigo")
        self.assertEqual(insumo.preco, Decimal("1234.56"))

    def test_nome_duplicado_no_mesmo_cliente_e_rejeitado(self):
        Insumo.objects.create(
            cliente=self.cliente, nome="Farinha de Trigo", categoria=self.categoria,
            unidade_medida=Insumo.UNIDADE_KG, quantidade=Decimal("1"), preco=Decimal("1"),
        )
        resposta = self.client.post(reverse("insumo-create"), self._payload())
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Insumo.objects.filter(nome="Farinha de Trigo").count(), 1)

    def test_categoria_de_outro_cliente_e_rejeitada(self):
        _, _, categoria_de_outro_cliente = criar_cliente_com_acesso_total("Z", "11444777000161")
        resposta = self.client.post(reverse("insumo-create"), self._payload(categoria=categoria_de_outro_cliente.pk))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_cadastro_valido_funciona(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload())
        self.assertRedirects(resposta, reverse("insumo"))
        self.assertTrue(Insumo.objects.filter(nome="Farinha de Trigo", cliente=self.cliente).exists())


class InsumoListagemTests(TestCase):
    """Pesquisa, ordenação e paginação da listagem."""

    def setUp(self):
        self.cliente, self.usuario, self.categoria = criar_cliente_com_acesso_total("B", "11222333000181")
        self.client.login(username="usuario_b", password="SenhaForte123!")
        for i in range(3):
            Insumo.objects.create(
                cliente=self.cliente, nome=f"Insumo {i}", categoria=self.categoria,
                unidade_medida=Insumo.UNIDADE_UN, quantidade=Decimal("1"), preco=Decimal(str(i)),
            )

    def test_pesquisa_filtra_por_nome(self):
        resposta = self.client.get(reverse("insumo"), {"q": "Insumo 1"})
        insumos = list(resposta.context["insumos"])
        self.assertEqual(len(insumos), 1)
        self.assertEqual(insumos[0].nome, "Insumo 1")

    def test_ordenacao_por_preco_desc(self):
        resposta = self.client.get(reverse("insumo"), {"sort": "preco", "dir": "desc"})
        nomes = [i.nome for i in resposta.context["insumos"]]
        self.assertEqual(nomes, ["Insumo 2", "Insumo 1", "Insumo 0"])

    def test_coluna_de_ordenacao_invalida_nao_quebra_e_usa_padrao(self):
        resposta = self.client.get(reverse("insumo"), {"sort": "cliente__cpf_cnpj"})
        self.assertEqual(resposta.status_code, 200)

    def test_paginacao_respeita_per_page(self):
        # 10 é o menor valor da whitelist (OPCOES_POR_PAGINA); precisamos de
        # mais de 10 registros para a segunda página existir de verdade.
        for i in range(3, 12):
            Insumo.objects.create(
                cliente=self.cliente, nome=f"Insumo {i}", categoria=self.categoria,
                unidade_medida=Insumo.UNIDADE_UN, quantidade=Decimal("1"), preco=Decimal(str(i)),
            )
        resposta = self.client.get(reverse("insumo"), {"per_page": "10"})
        self.assertEqual(len(resposta.context["insumos"]), 10)
        self.assertTrue(resposta.context["page_obj"].has_next())

    def test_per_page_fora_da_whitelist_usa_padrao(self):
        resposta = self.client.get(reverse("insumo"), {"per_page": "999999"})
        self.assertEqual(resposta.context["per_page"], 25)

    def test_exportacao_excel_retorna_arquivo(self):
        resposta = self.client.get(reverse("insumo"), {"export": "xlsx"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_exportacao_pdf_retorna_arquivo(self):
        resposta = self.client.get(reverse("insumo"), {"export": "pdf"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")


class InsumoExclusaoLogicaTests(TestCase):
    def setUp(self):
        self.cliente, self.usuario, self.categoria = criar_cliente_com_acesso_total("C", "11222333000181")
        self.client.login(username="usuario_c", password="SenhaForte123!")
        self.insumo = Insumo.objects.create(
            cliente=self.cliente, nome="Sal", categoria=self.categoria,
            unidade_medida=Insumo.UNIDADE_KG, quantidade=Decimal("1"), preco=Decimal("2"),
        )

    def test_excluir_marca_como_inativo_sem_apagar(self):
        self.client.post(reverse("insumo-excluir", args=[self.insumo.pk]))
        self.insumo.refresh_from_db()
        self.assertFalse(self.insumo.ativo)
        self.assertTrue(Insumo.objects.filter(pk=self.insumo.pk).exists())

    def test_insumo_excluido_nao_aparece_na_listagem(self):
        self.client.post(reverse("insumo-excluir", args=[self.insumo.pk]))
        resposta = self.client.get(reverse("insumo"))
        self.assertNotIn(self.insumo, list(resposta.context["insumos"]))


class InsumoSegurancaMultiTenantTests(TestCase):
    """Um usuário do Cliente B nunca deve ver, editar ou excluir um
    registro do Cliente A (proteção contra IDOR)."""

    def setUp(self):
        self.cliente_a, self.usuario_a, self.categoria_a = criar_cliente_com_acesso_total("A", "11222333000181")
        self.cliente_b, self.usuario_b, self.categoria_b = criar_cliente_com_acesso_total("B", "11444777000161")
        self.insumo_a = Insumo.objects.create(
            cliente=self.cliente_a, nome="Insumo do Cliente A", categoria=self.categoria_a,
            unidade_medida=Insumo.UNIDADE_UN, quantidade=Decimal("1"), preco=Decimal("1"),
        )
        self.client.login(username="usuario_b", password="SenhaForte123!")

    def test_insumo_de_outro_cliente_nao_aparece_na_listagem(self):
        resposta = self.client.get(reverse("insumo"))
        self.assertNotIn(self.insumo_a, list(resposta.context["insumos"]))

    def test_editar_insumo_de_outro_cliente_retorna_404(self):
        resposta = self.client.get(reverse("insumo-edit", args=[self.insumo_a.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_excluir_insumo_de_outro_cliente_retorna_404_e_nao_altera_nada(self):
        resposta = self.client.post(reverse("insumo-excluir", args=[self.insumo_a.pk]))
        self.assertEqual(resposta.status_code, 404)
        self.insumo_a.refresh_from_db()
        self.assertTrue(self.insumo_a.ativo)

    def test_nao_consegue_usar_categoria_de_outro_cliente_ao_editar_proprio_insumo(self):
        insumo_b = Insumo.objects.create(
            cliente=self.cliente_b, nome="Insumo do Cliente B", categoria=self.categoria_b,
            unidade_medida=Insumo.UNIDADE_UN, quantidade=Decimal("1"), preco=Decimal("1"),
        )
        resposta = self.client.post(
            reverse("insumo-edit", args=[insumo_b.pk]),
            {
                "nome": "Insumo do Cliente B",
                "categoria": self.categoria_a.pk,  # categoria do Cliente A!
                "unidade_medida": Insumo.UNIDADE_UN,
                "quantidade": "1",
                "preco": "1",
                "ativo": "on",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        insumo_b.refresh_from_db()
        self.assertEqual(insumo_b.categoria_id, self.categoria_b.pk)


class InsumoPermissaoTests(TestCase):
    """O acesso ao módulo já é coberto por apps/modulo/tests.py — aqui
    testamos que a ação específica (excluir) exige a permissão certa."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa Sem Permissao"
        )
        modulo, _ = Modulo.objects.get_or_create(slug="insumo", defaults={"nome": "Insumos", "ativo": True})
        ClienteModulo.objects.create(cliente=self.cliente, modulo=modulo, ativo=True)

        # Grupo só com permissão de VISUALIZAR — não pode excluir.
        grupo = Group.objects.create(name="Somente Visualizar")
        grupo.permissions.add(Permission.objects.get(content_type__app_label="insumo", codename="view_insumo"))

        self.usuario = User.objects.create_user(
            username="visualizador", email="visualizador@teste.local", password="SenhaForte123!"
        )
        self.usuario.profile.cliente = self.cliente
        self.usuario.profile.save()
        self.usuario.groups.add(grupo)

        self.categoria = Categoria.objects.create(cliente=self.cliente, nome="Categoria")
        self.insumo = Insumo.objects.create(
            cliente=self.cliente, nome="Insumo", categoria=self.categoria,
            unidade_medida=Insumo.UNIDADE_UN, quantidade=Decimal("1"), preco=Decimal("1"),
        )
        self.client.login(username="visualizador", password="SenhaForte123!")

    def test_usuario_sem_permissao_de_excluir_recebe_403(self):
        resposta = self.client.post(reverse("insumo-excluir", args=[self.insumo.pk]))
        self.assertEqual(resposta.status_code, 403)
        self.insumo.refresh_from_db()
        self.assertTrue(self.insumo.ativo)

    def test_usuario_sem_permissao_de_criar_recebe_403(self):
        resposta = self.client.get(reverse("insumo-create"))
        self.assertEqual(resposta.status_code, 403)
