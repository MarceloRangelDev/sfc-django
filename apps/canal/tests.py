from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente

from .models import CanalTaxa, CanalVenda


def cliente(nome, cnpj, matriz=None):
    return Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )


def usuario(nome, c, acoes=("view", "add", "change"), filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="canal", codename__in=[f"{a}_canalvenda" for a in acoes]))
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


def dados(nome="IFOOD", descricao="MOTOBOY PLATAFORMA - com antecipação", taxas=(("Comissão", "26,20", ""), ("Antecipação", "1,59", "")), **extra):
    base = {
        "nome": nome, "descricao": descricao, "ativo": "on",
        "taxas-TOTAL_FORMS": str(len(taxas)), "taxas-INITIAL_FORMS": "0", "taxas-MIN_NUM_FORMS": "0", "taxas-MAX_NUM_FORMS": "1000",
    }
    for i, (desc, pct, fixo) in enumerate(taxas):
        base.update({f"taxas-{i}-descricao": desc, f"taxas-{i}-percentual": pct, f"taxas-{i}-valor_fixo": fixo})
    return {**base, **extra}


class CanalTelasTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("u", self.c)
        self.client.force_login(self.user)

    def test_exemplo_ifood_totais_e_liquido(self):
        resp = self.client.post(reverse("canal-create"), dados())
        self.assertRedirects(resp, reverse("canal-list"), fetch_redirect_response=False)
        canal = CanalVenda.objects.get()
        self.assertEqual(canal.taxa_percentual_total, Decimal("27.79"))
        self.assertEqual(canal.liquido_percentual, Decimal("72.21"))
        self.assertEqual((canal.taxa_percentual_formatada, canal.liquido_formatado), ("27,79%", "72,21%"))
        self.assertEqual(canal.descricao_das_taxas, "26,20% (Comissão) + 1,59% (Antecipação)")
        self.assertContains(self.client.get(reverse("canal-list")), "27,79%")

    def test_taxas_fixas_e_percentuais_sao_somadas_separadamente(self):
        self.client.post(reverse("canal-create"), dados(taxas=(("Comissão", "10", ""), ("Embalagem", "", "1,50"), ("Entrega", "", "2,00"))))
        canal = CanalVenda.objects.get()
        self.assertEqual((canal.taxa_percentual_total, canal.taxa_fixa_total), (Decimal("10"), 350))
        self.assertEqual(canal.descricao_das_taxas, "10,00% (Comissão) + R$ 1,50 (Embalagem) + R$ 2,00 (Entrega)")

    def test_canal_sem_taxas_tem_totais_zerados(self):
        self.client.post(reverse("canal-create"), dados(nome="Balcão", descricao="", taxas=()))
        canal = CanalVenda.objects.get()
        self.assertEqual((canal.taxa_percentual_total, canal.taxa_fixa_total, canal.liquido_percentual), (0, 0, 100))
        self.assertEqual(canal.descricao_das_taxas, "Sem taxas")

    def test_taxa_precisa_de_um_unico_valor(self):
        for taxa in (("X", "", ""), ("X", "5", "1,00")):
            resp = self.client.post(reverse("canal-create"), dados(taxas=(taxa,)))
            self.assertEqual(resp.status_code, 200)
        self.assertFalse(CanalVenda.objects.exists())

    def test_soma_percentual_nao_pode_chegar_a_100(self):
        resp = self.client.post(reverse("canal-create"), dados(taxas=(("A", "60", ""), ("B", "40", ""))))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "menor que 100%")
        self.assertFalse(CanalVenda.objects.exists())

    def test_percentual_invalido(self):
        for valor in ("-1", "100", "abc"):
            self.assertEqual(self.client.post(reverse("canal-create"), dados(taxas=(("A", valor, ""),))).status_code, 200)
        self.assertFalse(CanalVenda.objects.exists())

    def test_check_constraint_do_banco(self):
        canal = CanalVenda.objects.create(cliente=self.c, nome="X")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CanalTaxa.objects.create(canal=canal, percentual=Decimal("1"), valor_fixo=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CanalTaxa.objects.create(canal=canal)

    def test_nome_e_descricao_unicos_por_unidade(self):
        self.client.post(reverse("canal-create"), dados())
        resp = self.client.post(reverse("canal-create"), dados(nome="ifood"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.post(reverse("canal-create"), dados(descricao="OUTRA")).status_code, 302)

    def test_editar_substitui_taxas_e_audita(self):
        self.client.post(reverse("canal-create"), dados())
        canal = CanalVenda.objects.get()
        taxa = canal.taxas.first()
        edicao = {
            "nome": "IFOOD", "descricao": canal.descricao, "ativo": "on",
            "taxas-TOTAL_FORMS": "1", "taxas-INITIAL_FORMS": "1", "taxas-MIN_NUM_FORMS": "0", "taxas-MAX_NUM_FORMS": "1000",
            "taxas-0-id": taxa.pk, "taxas-0-canal": canal.pk, "taxas-0-descricao": "Comissão", "taxas-0-percentual": "12", "taxas-0-valor_fixo": "",
        }
        self.assertEqual(self.client.post(reverse("canal-edit", args=[canal.pk]), edicao).status_code, 302)
        canal.refresh_from_db()
        self.assertEqual(canal.taxa_percentual_total, Decimal("12"))
        self.assertEqual(canal.taxas.count(), 1)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="canal", acao="editar").exists())

    def test_salvar_e_adicionar_outro(self):
        resp = self.client.post(reverse("canal-create"), dados(salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("canal-create"), fetch_redirect_response=False)

    def test_filtros_lista(self):
        CanalVenda.objects.create(cliente=self.c, nome="IFOOD", descricao="Loja")
        CanalVenda.objects.create(cliente=self.c, nome="Balcão", ativo=False)
        nomes = lambda **p: {c.nome for c in self.client.get(reverse("canal-list"), p).context["canais"]}
        self.assertEqual(nomes(), {"IFOOD"})
        self.assertEqual(nomes(status="inativo"), {"Balcão"})
        self.assertEqual(nomes(status="todos"), {"IFOOD", "Balcão"})
        self.assertEqual(nomes(q="loja"), {"IFOOD"})

    def test_permissoes(self):
        canal = CanalVenda.objects.create(cliente=self.c, nome="X")
        self.client.force_login(usuario("v", self.c, ("view",)))
        self.assertEqual(self.client.get(reverse("canal-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("canal-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("canal-edit", args=[canal.pk])).status_code, 403)
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.get(reverse("canal-list")).status_code, 403)


class CanalMultiTenantTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.m = CanalVenda.objects.create(cliente=self.matriz, nome="Da matriz")
        self.f = CanalVenda.objects.create(cliente=self.filial, nome="Da filial")
        self.o = CanalVenda.objects.create(cliente=self.outra, nome="Da outra")

    def nomes(self, user, **p):
        self.client.force_login(user)
        return {c.nome for c in self.client.get(reverse("canal-list"), p).context["canais"]}

    def test_outra_empresa_nunca_aparece_nem_edita(self):
        u = usuario("m", self.matriz, filiais=("view", "change"))
        self.assertEqual(self.nomes(u), {"Da matriz", "Da filial"})
        self.assertEqual(self.client.get(reverse("canal-edit", args=[self.o.pk])).status_code, 404)

    def test_matriz_so_alcanca_filial_com_permissao(self):
        self.assertEqual(self.nomes(usuario("m", self.matriz)), {"Da matriz"})
        self.client.force_login(usuario("m2", self.matriz, filiais=("view",)))
        self.assertEqual(self.client.get(reverse("canal-edit", args=[self.f.pk])).status_code, 404)  # falta change_itensfilial

    def test_filial_nao_ve_a_matriz(self):
        self.assertEqual(self.nomes(usuario("f", self.filial, filiais=("view", "change"))), {"Da filial"})

    def test_filtro_unidade(self):
        u = usuario("m", self.matriz, filiais=("view",))
        self.assertEqual(self.nomes(u, unidade=self.filial.pk), {"Da filial"})
