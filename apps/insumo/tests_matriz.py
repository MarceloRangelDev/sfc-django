"""Matriz x filiais: a matriz alcança os itens das filiais conforme a permissão
`cliente.<acao>_itensfilial`; filial e outras empresas nunca saem do próprio escopo."""
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

ACOES = ("view", "add", "change", "delete")


def cliente(nome, cnpj, filial_de=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if filial_de else Cliente.TIPO_MATRIZ, matriz=filial_de,
    )
    habilitar_modulo(c, "insumo")
    habilitar_modulo(c, "categoria")
    return c


def usuario(nome, c, filiais=()):
    """Usuário com todas as permissões de insumo/categoria e, em `filiais`, as ações sobre itens de filiais."""
    grupo = Group.objects.create(name=f"G {nome}")
    perms = Permission.objects.filter(content_type__app_label__in=["insumo", "categoria"])
    extras = Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set([*perms, *extras])
    u = User.objects.create_user(username=nome, email=f"{nome}@teste.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


class ItensMatrizFilialTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz A", "11222333000181")
        self.filial = cliente("Filial A1", "11444777000161", self.matriz)
        self.filial2 = cliente("Filial A2", "45723174000110", self.matriz)
        self.outra = cliente("Outra Empresa", "34028316000103")
        self.i_matriz = novo_insumo(self.matriz, "Do matriz")
        self.i_filial = novo_insumo(self.filial, "Da filial")
        self.i_outra = novo_insumo(self.outra, "Da outra")

    def nomes(self, user, **params):
        self.client.force_login(user)
        resp = self.client.get(reverse("insumo"), params)
        self.assertEqual(resp.status_code, 200)
        return {i.nome for i in resp.context["insumos"]}, resp

    def test_matriz_com_permissao_ve_matriz_e_filiais_mas_nao_outra_empresa(self):
        u = usuario("gm", self.matriz, ["view"])
        nomes, resp = self.nomes(u)
        self.assertEqual(nomes, {"Do matriz", "Da filial"})
        self.assertTrue(resp.context["mostrar_unidade"])
        self.assertContains(resp, 'title="Filial: Filial A1"')
        self.assertContains(resp, 'title="Matriz: Matriz A"')

    def test_matriz_sem_permissao_ve_so_os_proprios(self):
        nomes, resp = self.nomes(usuario("gm", self.matriz))
        self.assertEqual(nomes, {"Do matriz"})
        self.assertFalse(resp.context["mostrar_unidade"])

    def test_filial_nunca_ve_matriz_nem_irmas(self):
        novo_insumo(self.filial2, "Da irma")
        nomes, _ = self.nomes(usuario("gf", self.filial, ACOES))
        self.assertEqual(nomes, {"Da filial"})

    def test_filtro_por_unidade_e_unidade_de_fora_nao_vaza(self):
        u = usuario("gm", self.matriz, ["view"])
        self.assertEqual(self.nomes(u, unidade=self.filial.pk)[0], {"Da filial"})
        self.assertEqual(self.nomes(u, unidade=self.matriz.pk)[0], {"Do matriz"})
        self.assertEqual(self.nomes(u, unidade=self.outra.pk)[0], set())
        self.assertEqual(self.nomes(u, unidade="abc")[0], {"Do matriz", "Da filial"})

    def test_editar_filial_exige_permissao(self):
        url = reverse("insumo-edit", args=[self.i_filial.pk])
        self.client.force_login(usuario("sem", self.matriz, ["view"]))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(usuario("com", self.matriz, ["change"]))
        self.assertEqual(self.client.get(url).status_code, 200)
        dados = {"nome": "Renomeado", "medida": medida("un").pk, "quantidade": "1", "preco_pago": "1,00", "ativo": "on"}
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.i_filial.refresh_from_db()
        self.assertEqual((self.i_filial.nome, self.i_filial.cliente_id), ("Renomeado", self.filial.pk))  # dono não muda

    def test_excluir_filial_exige_permissao_e_nao_alcanca_outra_empresa(self):
        self.client.force_login(usuario("sem", self.matriz, ["view", "change"]))
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.i_filial.pk])).status_code, 404)
        u = usuario("com", self.matriz, ["delete"])
        self.client.force_login(u)
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.i_outra.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.i_filial.pk])).status_code, 302)
        self.i_filial.refresh_from_db()
        self.assertFalse(self.i_filial.ativo)

    def test_filial_nao_edita_nem_exclui_item_da_matriz(self):
        self.client.force_login(usuario("gf", self.filial, ACOES))
        self.assertEqual(self.client.get(reverse("insumo-edit", args=[self.i_matriz.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.i_matriz.pk])).status_code, 404)

    def test_criar_em_filial(self):
        dados = {"nome": "Novo", "medida": medida("un").pk, "quantidade": "1", "preco_pago": "1,00", "ativo": "on"}
        url = reverse("insumo-create")
        # sem permissão de criar em filiais: sem campo Unidade e a escolha é ignorada (fica na matriz)
        self.client.force_login(usuario("sem", self.matriz, ["view"]))
        self.assertNotIn("cliente", self.client.get(url).context["form"].fields)
        self.client.post(url, {**dados, "cliente": self.filial.pk})
        self.assertEqual(Insumo.objects.get(nome="Novo").cliente_id, self.matriz.pk)
        Insumo.objects.filter(nome="Novo").delete()
        # com permissão: pode escolher filial própria, mas não uma filial de outra empresa
        self.client.force_login(usuario("com", self.matriz, ["add"]))
        self.assertIn("cliente", self.client.get(url).context["form"].fields)
        resp = self.client.post(url, {**dados, "cliente": self.outra.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nome="Novo").exists())
        self.assertEqual(self.client.post(url, {**dados, "cliente": self.filial.pk}).status_code, 302)
        self.assertEqual(Insumo.objects.get(nome="Novo").cliente_id, self.filial.pk)

    def test_nome_repetido_so_conflita_na_mesma_unidade(self):
        self.client.force_login(usuario("com", self.matriz, ["add"]))
        dados = {"nome": "Do matriz", "medida": medida("un").pk, "quantidade": "1", "preco_pago": "1,00", "ativo": "on"}
        url = reverse("insumo-create")
        self.assertEqual(self.client.post(url, {**dados, "cliente": self.matriz.pk}).status_code, 200)  # duplicado
        self.assertEqual(self.client.post(url, {**dados, "cliente": self.filial2.pk}).status_code, 302)

    def test_categorias_seguem_o_mesmo_escopo(self):
        Categoria.objects.create(cliente=self.matriz, nome="C matriz")
        Categoria.objects.create(cliente=self.filial, nome="C filial")
        Categoria.objects.create(cliente=self.outra, nome="C outra")
        self.client.force_login(usuario("gm", self.matriz, ["view"]))
        resp = self.client.get(reverse("categoria"))
        self.assertEqual({c.nome for c in resp.context["categorias"]}, {"C matriz", "C filial"})
        resp = self.client.get(reverse("categoria"), {"unidade": self.filial.pk})
        self.assertEqual({c.nome for c in resp.context["categorias"]}, {"C filial"})
