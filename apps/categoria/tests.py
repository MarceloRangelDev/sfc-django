from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.insumo.tests import novo_insumo
from web_project.testing import habilitar_modulo

from .calculos import percentual_do_total, ticket_medio_centavos
from .models import Categoria


def cliente(nome, cnpj):
    c = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome)
    habilitar_modulo(c, "categoria")
    return c


def usuario(nome, c, acoes=("view", "add", "change", "delete")):
    grupo = Group.objects.create(name=f"G {nome}")
    grupo.permissions.set(Permission.objects.filter(
        content_type__app_label="categoria", codename__in=[f"{a}_categoria" for a in acoes]))
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


class CalculosTests(TestCase):
    def test_ticket_medio(self):
        self.assertEqual(ticket_medio_centavos(10000, 3), 3333)  # R$ 33,33

    def test_sem_vendas_valores_neutros(self):
        self.assertEqual(ticket_medio_centavos(0, 0), 0)
        self.assertEqual(ticket_medio_centavos(5000, 0), 0)  # sem divisão por zero
        self.assertEqual(percentual_do_total(0, 0), Decimal("0.00"))

    def test_percentual_do_total(self):
        self.assertEqual(percentual_do_total(2500, 10000), Decimal("25.00"))
        self.assertEqual(percentual_do_total(1, 3), Decimal("33.33"))


class TelasCategoriaTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.c)
        self.client.force_login(self.user)
        self.a = Categoria.objects.create(cliente=self.c, nome="Bebidas", quantidade_vendida=10, faturamento=30000)
        self.b = Categoria.objects.create(cliente=self.c, nome="Doces", quantidade_vendida=20, faturamento=10000)

    def test_lista_mostra_metricas(self):
        resp = self.client.get(reverse("categoria"))
        a = next(c for c in resp.context["categorias"] if c.nome == "Bebidas")
        self.assertEqual((a.ticket_medio, a.percentual), ("R$ 30,00", Decimal("75.00")))
        self.assertEqual(resp.context["total_faturamento"], "R$ 400,00")
        self.assertEqual(resp.context["total_ticket"], "R$ 13,33")

    def test_lista_sem_faturamento_nao_quebra(self):
        Categoria.objects.update(quantidade_vendida=0, faturamento=0)
        resp = self.client.get(reverse("categoria"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(c.percentual == 0 and c.ticket_medio == "R$ 0,00" for c in resp.context["categorias"]))

    def test_filtros(self):
        self.b.ativo = False
        self.b.save()
        nomes = lambda **p: {c.nome for c in self.client.get(reverse("categoria"), p).context["categorias"]}
        self.assertEqual(nomes(status="ativo"), {"Bebidas"})
        self.assertEqual(nomes(status="inativo"), {"Doces"})
        self.assertEqual(nomes(q="doc"), {"Doces"})

    def test_ativar_e_desativar(self):
        url = reverse("categoria-alternar-ativo", args=[self.a.pk])
        self.client.post(url)
        self.a.refresh_from_db()
        self.assertFalse(self.a.ativo)
        self.client.post(url)
        self.a.refresh_from_db()
        self.assertTrue(self.a.ativo)

    def test_excluir_sem_uso_apaga_e_em_uso_so_desativa(self):
        self.client.post(reverse("categoria-excluir", args=[self.b.pk]))
        self.assertFalse(Categoria.objects.filter(pk=self.b.pk).exists())
        # Insumo antigo ainda aponta para a categoria (FK oculta): conta como uso.
        insumo = novo_insumo(self.c, "Suco")
        insumo.categoria = self.a
        insumo.save(update_fields=["categoria"])
        self.client.post(reverse("categoria-excluir", args=[self.a.pk]))
        self.a.refresh_from_db()
        self.assertFalse(self.a.ativo)

    def test_permissoes_sao_exigidas(self):
        so_ver = usuario("v", self.c, ("view",))
        self.client.force_login(so_ver)
        self.assertEqual(self.client.post(reverse("categoria-excluir", args=[self.a.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("categoria-alternar-ativo", args=[self.a.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("categoria-create")).status_code, 403)

    def test_outro_cliente_da_404(self):
        alheia = Categoria.objects.create(cliente=self.outra, nome="Alheia")
        self.assertEqual(self.client.post(reverse("categoria-excluir", args=[alheia.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("categoria-alternar-ativo", args=[alheia.pk])).status_code, 404)
        self.assertTrue(Categoria.objects.filter(pk=alheia.pk).exists())

    def test_criar_com_dados_de_venda_e_salvar_e_adicionar_outra(self):
        dados = {"nome": "Salgados", "quantidade_vendida": "5", "faturamento": "100,00", "ativo": "on"}
        resp = self.client.post(reverse("categoria-create"), {**dados, "salvar_e_novo": "1"})
        self.assertRedirects(resp, reverse("categoria-create"), fetch_redirect_response=False)
        nova = Categoria.objects.get(nome="Salgados")
        self.assertEqual((nova.quantidade_vendida, nova.faturamento), (5, 10000))

    def test_valores_invalidos_sao_recusados(self):
        base = {"nome": "X", "ativo": "on"}
        for extra in ({"quantidade_vendida": "-1", "faturamento": "1"}, {"quantidade_vendida": "1", "faturamento": "abc"}):
            self.assertEqual(self.client.post(reverse("categoria-create"), {**base, **extra}).status_code, 200)
        self.assertFalse(Categoria.objects.filter(nome="X").exists())

    def test_previa_do_servidor(self):
        resp = self.client.get(reverse("categoria-calcular"), {"quantidade": "4", "faturamento": "100,00", "pk": self.a.pk})
        dados = resp.json()
        self.assertEqual(dados["ticket_medio"], "R$ 25,00")
        self.assertEqual(dados["percentual"], "50,00")  # R$ 100 / (R$ 100 + R$ 100 da outra)
        vazio = self.client.get(reverse("categoria-calcular"), {"quantidade": "0", "faturamento": "x"}).json()
        self.assertEqual(vazio, {"ticket_medio": "R$ 0,00", "percentual": "0,00"})
