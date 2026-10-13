from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.canal.models import CanalTaxa, CanalVenda
from apps.canal.services import salvar_canal
from apps.cliente.models import Cliente
from apps.ficha.tests import criar as criar_ficha
from apps.ficha.tests import produto as criar_produto
from apps.insumo.tests import novo_insumo
from apps.processado.tests import criar as criar_processado
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from .calculos import METODO_MARGEM, METODO_MARKUP, METODO_VALOR, calcular_preco
from .models import Precificacao
from .services import preencher, salvar_precificacao

D = Decimal


class FormulasTests(SimpleTestCase):
    """Custo R$ 10,00 (1000 centavos) nos exemplos."""

    def test_markup_sobre_o_custo_sem_canal(self):
        r = calcular_preco(1000, 0, D("0"), 0, METODO_MARKUP, D("50"))
        self.assertEqual((r.preco_venda, r.lucro), (1500, 500))

    def test_margem_sobre_o_preco_sem_canal(self):
        r = calcular_preco(1000, 0, D("0"), 0, METODO_MARGEM, D("20"))
        self.assertEqual((r.preco_venda, r.lucro), (1250, 250))  # 250/1250 = 20 % do PREÇO

    def test_margem_e_markup_dao_resultados_diferentes(self):
        margem = calcular_preco(1000, 0, D("0"), 0, METODO_MARGEM, D("50"))
        markup = calcular_preco(1000, 0, D("0"), 0, METODO_MARKUP, D("50"))
        self.assertEqual((margem.preco_venda, markup.preco_venda), (2000, 1500))

    def test_valor_em_reais(self):
        r = calcular_preco(1000, 0, D("0"), 0, METODO_VALOR, valor_lucro=300)
        self.assertEqual((r.preco_venda, r.lucro), (1300, 300))

    def test_canal_com_taxa_percentual_e_fixa_markup(self):
        # P = (1000 × 1,5 + 100) ÷ (1 − 0,10) = 1777,78 → 1778; taxas 10 % = 178; lucro = 1778 − 1000 − 100 − 178
        r = calcular_preco(1000, 0, D("10"), 100, METODO_MARKUP, D("50"))
        self.assertEqual((r.preco_venda, r.taxas_percentuais_valor, r.lucro), (1778, 178, 500))

    def test_margem_com_canal_o_lucro_e_m_por_cento_do_preco(self):
        # P = 1000 ÷ (1 − 0,2779 − 0,20) = 1915,14 → 1916; lucro ≈ 20 % de P
        r = calcular_preco(1000, 0, D("27.79"), 0, METODO_MARGEM, D("20"))
        self.assertEqual(r.preco_venda, 1916)
        self.assertAlmostEqual(r.lucro / r.preco_venda, 0.20, delta=0.001)

    def test_custo_operacional_entra_no_custo_total(self):
        r = calcular_preco(1000, 200, D("0"), 0, METODO_VALOR, valor_lucro=300)
        self.assertEqual((r.custo_total, r.preco_venda), (1200, 1500))

    def test_decomposicao_fecha_exatamente_o_preco(self):
        for metodo, perc, valor in ((METODO_MARGEM, "33.33", 0), (METODO_MARKUP, "37.5", 0), (METODO_VALOR, "0", 777)):
            for taxa in ("0", "13.7", "27.79"):
                r = calcular_preco(1234, 321, D(taxa), 59, metodo, D(perc), valor)
                self.assertEqual(r.custo_total + r.taxa_fixa + r.taxas_percentuais_valor + r.lucro, r.preco_venda)

    def test_preco_arredonda_para_cima_e_nunca_perde_margem(self):
        r = calcular_preco(1000, 0, D("0"), 0, METODO_MARGEM, D("30"))  # 1428,57 → 1429
        self.assertEqual(r.preco_venda, 1429)
        self.assertGreaterEqual(r.lucro, 428)

    def test_cmv_e_o_custo_da_ficha_sobre_o_preco(self):
        r = calcular_preco(1000, 500, D("0"), 0, METODO_VALOR, valor_lucro=500)  # P = 2000
        self.assertEqual(r.cmv_percentual, D("50.00"))  # só a ficha (1000), não o custo operacional

    def test_sem_custo_e_sem_lucro_nao_divide_por_zero(self):
        r = calcular_preco(0, 0, D("0"), 0, METODO_VALOR)
        self.assertEqual((r.preco_venda, r.cmv_percentual, r.lucro), (0, D("0.00"), 0))
        self.assertEqual(calcular_preco(0, 0, D("10"), 0, METODO_MARKUP, D("50")).preco_venda, 0)

    def test_taxas_zeradas_equivalem_a_sem_canal(self):
        a = calcular_preco(1000, 0, D("0"), 0, METODO_MARKUP, D("25"))
        self.assertEqual((a.taxas_percentuais_valor, a.taxa_fixa, a.preco_venda), (0, 0, 1250))

    def test_soma_de_margem_e_taxas_maior_ou_igual_a_100_e_erro(self):
        with self.assertRaises(RegraNegocioError):
            calcular_preco(1000, 0, D("60"), 0, METODO_MARGEM, D("40"))
        with self.assertRaises(RegraNegocioError):
            calcular_preco(1000, 0, D("10"), 0, METODO_MARGEM, D("95"))

    def test_entradas_invalidas(self):
        for args in (
            (-1, 0, D("0"), 0, METODO_VALOR), (1000, 0, D("100"), 0, METODO_VALOR), (1000, 0, D("-1"), 0, METODO_VALOR),
            (1000, 0, D("0"), 0, "outro"), (1000.5, 0, D("0"), 0, METODO_VALOR), (10**16, 0, D("0"), 0, METODO_VALOR),
        ):
            with self.assertRaises(RegraNegocioError):
                calcular_preco(*args)
        with self.assertRaises(RegraNegocioError):
            calcular_preco(1000, 0, D("0"), 0, METODO_MARKUP, D("-5"))


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in ("insumo", "processado", "produto", "ficha", "precificacao", "categoria"):
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, acoes=("view", "add", "change", "delete"), filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="precificacao", codename__in=[f"{a}_precificacao" for a in acoes]))
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


def novo_canal(c, nome="IFOOD", descricao="Motoboy", taxas=(("Comissão", "26.20", None), ("Antecipação", "1.59", None))):
    canal = CanalVenda(cliente=c, nome=nome, descricao=descricao)
    linhas = [CanalTaxa(descricao=d, percentual=D(p) if p else None, valor_fixo=f) for d, p, f in taxas]
    salvar_canal(None, canal, linhas)
    return canal


def nova_precificacao(c, ficha, canal=None, **extra):
    padrao = dict(metodo_lucro=METODO_MARKUP, percentual_lucro=D("50"), cliente=c, ficha=ficha, canal=canal)
    p = Precificacao(**{**padrao, **extra})
    salvar_precificacao(None, p)
    p.refresh_from_db()
    return p


class ServicosETransacaoTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)  # R$ 10,00/kg
        self.ficha = criar_ficha(self.c, "Bolo", [(self.farinha, "kg", "1")])  # custo R$ 10,00

    def test_calcula_e_copia_produto_da_ficha(self):
        p = nova_precificacao(self.c, self.ficha)
        self.assertEqual((p.produto_id, p.custo_ficha, p.preco_venda), (self.ficha.produto_id, 1000, 1500))

    def test_varias_precificacoes_para_o_mesmo_produto(self):
        nova_precificacao(self.c, self.ficha, canal_nome="Balcão")  # canal personalizado
        nova_precificacao(self.c, self.ficha, canal=novo_canal(self.c), percentual_lucro=D("80"))
        nova_precificacao(self.c, self.ficha, canal=novo_canal(self.c, "Site", ""), percentual_lucro=D("30"))
        self.assertEqual(Precificacao.objects.filter(produto=self.ficha.produto).count(), 3)

    def test_identificacao_gerada_pela_ficha_e_pelo_canal_sem_nome_digitado(self):
        canal = novo_canal(self.c)
        self.assertEqual(nova_precificacao(self.c, self.ficha, canal=canal).nome, "Bolo — IFOOD | Motoboy")
        self.assertEqual(nova_precificacao(self.c, self.ficha, canal_nome="Balcão especial").nome, "Bolo — Balcão especial")
        # a mesma ficha e o mesmo canal podem ter mais de uma precificação (margens diferentes)
        outra = nova_precificacao(self.c, self.ficha, canal=canal, percentual_lucro=D("90"))
        self.assertEqual(outra.nome, "Bolo — IFOOD | Motoboy")
        self.assertEqual(Precificacao.objects.filter(canal=canal).count(), 2)
        self.ficha.nome = "Bolo grande"
        self.ficha.save(update_fields=["nome"])
        self.farinha.preco_pago = 1100
        self.farinha.save()  # o recálculo regrava o rótulo
        self.assertEqual(Precificacao.objects.get(pk=outra.pk).nome, "Bolo grande — IFOOD | Motoboy")

    def test_canal_cadastrado_copia_nome_e_taxas(self):
        canal = novo_canal(self.c)
        p = nova_precificacao(self.c, self.ficha, canal=canal)
        self.assertEqual((p.canal_nome, p.taxa_percentual), ("IFOOD", D("27.79")))

    def test_custo_do_insumo_propaga_ate_o_preco(self):
        p = nova_precificacao(self.c, self.ficha)
        self.farinha.preco_pago = 2000
        self.farinha.save()
        p.refresh_from_db()
        self.assertEqual((p.custo_ficha, p.preco_venda), (2000, 3000))

    def test_cadeia_insumo_processado_ficha_precificacao(self):
        massa = criar_processado(self.c, "Massa", [(self.farinha, "kg", "1")], "1", "kg")  # R$ 10,00/kg
        ficha = criar_ficha(self.c, "Torta", [(massa.insumo, "g", "500")])  # R$ 5,00
        p = nova_precificacao(self.c, ficha)
        self.assertEqual(p.preco_venda, 750)
        self.farinha.preco_pago = 2000  # farinha dobra -> massa -> ficha -> preço
        self.farinha.save()
        p.refresh_from_db()
        self.assertEqual((p.custo_ficha, p.preco_venda), (1000, 1500))

    def test_editar_taxas_do_canal_reprecifica(self):
        canal = novo_canal(self.c, taxas=(("Comissão", "10", None),))
        p = nova_precificacao(self.c, self.ficha, canal=canal)
        antes = p.preco_venda
        salvar_canal(None, canal, [CanalTaxa(descricao="Comissão", percentual=D("20"))], foto_antes=None)
        p.refresh_from_db()
        self.assertGreater(p.preco_venda, antes)
        self.assertEqual(p.taxa_percentual, D("20"))

    def test_taxa_do_canal_que_inviabiliza_a_margem_e_recusada_e_desfeita(self):
        from django.core.exceptions import ValidationError

        canal = novo_canal(self.c, taxas=(("Comissão", "10", None),))
        nova_precificacao(self.c, self.ficha, canal=canal, metodo_lucro=METODO_MARGEM, percentual_lucro=D("50"))
        with self.assertRaises(ValidationError):
            salvar_canal(None, canal, [CanalTaxa(descricao="Comissão", percentual=D("60"))])
        canal.refresh_from_db()
        self.assertEqual(canal.taxa_percentual_total, D("10"))  # nada mudou

    def test_recalculo_geral_refaz_precificacoes_velhas(self):
        from django.core.management import call_command

        p = nova_precificacao(self.c, self.ficha)
        Precificacao.objects.filter(pk=p.pk).update(preco_venda=1, custo_ficha=1)
        call_command("recalcular_custos", verbosity=0)
        p.refresh_from_db()
        self.assertEqual((p.custo_ficha, p.preco_venda), (1000, 1500))

    def test_precificacao_de_outro_cliente_nao_e_tocada(self):
        outro = cliente("Outro", "34028316000103")
        f2 = novo_insumo(outro, "Farinha", "kg", "1", 1000)
        p2 = nova_precificacao(outro, criar_ficha(outro, "Bolo", [(f2, "kg", "1")]))
        self.farinha.preco_pago = 9000
        self.farinha.save()
        p2.refresh_from_db()
        self.assertEqual(p2.preco_venda, 1500)

    def test_ficha_e_canal_em_uso_so_desativam(self):
        from web_project.mixins import em_uso

        canal = novo_canal(self.c)
        nova_precificacao(self.c, self.ficha, canal=canal)
        self.assertTrue(em_uso(self.ficha))
        self.assertTrue(em_uso(canal))

    def test_ficha_que_troca_de_produto_arrasta_a_precificacao(self):
        from apps.ficha.models import FichaItem
        from apps.ficha.services import salvar_ficha

        p = nova_precificacao(self.c, self.ficha)
        novo_produto = criar_produto(self.c, "Torta")
        ficha = self.ficha
        ficha.produto = novo_produto
        salvar_ficha(None, ficha, [FichaItem(insumo=self.farinha, medida=self.ficha.itens.first().medida, quantidade=D("1"))])
        p.refresh_from_db()
        self.assertEqual(p.produto_id, novo_produto.pk)


class TelasTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        self.ficha = criar_ficha(self.matriz, "Bolo", [(self.farinha, "kg", "1")])
        self.canal = novo_canal(self.matriz)

    def dados(self, **extra):
        base = {
            "ficha": self.ficha.pk, "canal": self.canal.pk, "canal_nome": "", "canal_descricao": "",
            "taxa_percentual": "", "taxa_fixa": "", "custo_operacional": "2,00", "metodo_lucro": "margem",
            "percentual_lucro": "20", "valor_lucro": "", "ativo": "on",
        }
        return {**base, **extra}

    def nomes(self, **p):
        """Fichas das precificações listadas (a identificação da precificação é a ficha)."""
        return [x.ficha.nome for x in self.client.get(reverse("precificacao"), p).context["precificacoes"]]

    def test_criar_com_canal_cadastrado(self):
        resp = self.client.post(reverse("precificacao-create"), self.dados())
        self.assertRedirects(resp, reverse("precificacao"), fetch_redirect_response=False)
        p = Precificacao.objects.get()
        # custo 1000 + 200 = 1200; P = 1200 ÷ (1 − 0,2779 − 0,20) = 2297,... ; margem ≈ 20 % do preço
        self.assertEqual((p.cliente_id, p.custo_total, p.canal_nome), (self.matriz.pk, 1200, "IFOOD"))
        self.assertAlmostEqual(p.lucro / p.preco_venda, 0.20, delta=0.001)
        self.assertContains(self.client.get(reverse("precificacao")), p.preco_venda_formatado)

    def test_criar_com_canal_personalizado(self):
        resp = self.client.post(reverse("precificacao-create"), self.dados(
            canal="", canal_nome="Balcão especial", canal_descricao="PIX", taxa_percentual="5", taxa_fixa="1,00",
            metodo_lucro="valor", percentual_lucro="", valor_lucro="3,00", custo_operacional="",
        ))
        self.assertEqual(resp.status_code, 302)
        p = Precificacao.objects.get()
        self.assertEqual((p.canal_id, p.canal_nome, p.taxa_percentual, p.taxa_fixa), (None, "Balcão especial", D("5"), 100))
        # P = (1000 + 300 + 100) ÷ 0,95 = 1473,68 → 1474
        self.assertEqual(p.preco_venda, 1474)
        self.assertEqual(p.nome_do_canal, "Balcão especial | PIX")

    def test_salvar_e_adicionar_outra(self):
        resp = self.client.post(reverse("precificacao-create"), {**self.dados(), "salvar_e_novo": "1"})
        self.assertRedirects(resp, reverse("precificacao-create"), fetch_redirect_response=False)

    def test_editar_nao_troca_dono_e_recalcula(self):
        self.client.post(reverse("precificacao-create"), self.dados())
        p = Precificacao.objects.get()
        resp = self.client.post(reverse("precificacao-edit", args=[p.pk]), self.dados(metodo_lucro="markup", percentual_lucro="100"))
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.metodo_lucro, p.cliente_id), ("markup", self.matriz.pk))

    def test_validacoes(self):
        casos = [
            self.dados(canal="", canal_nome=""),  # personalizado sem nome
            self.dados(percentual_lucro="100"),  # margem de 100 %
            self.dados(percentual_lucro="75"),  # margem + taxas do canal ≥ 100 %
            self.dados(percentual_lucro="-5"),
            self.dados(ficha=""),
            self.dados(canal="", canal_nome="X", taxa_percentual="100"),
            self.dados(custo_operacional="abc"),
        ]
        for dados in casos:
            self.assertEqual(self.client.post(reverse("precificacao-create"), dados).status_code, 200, dados)
        self.assertFalse(Precificacao.objects.exists())

    def test_ficha_ou_canal_de_outra_unidade_recusado(self):
        f_alheia = criar_ficha(self.outra, "Alheia", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        self.assertEqual(self.client.post(reverse("precificacao-create"), self.dados(ficha=f_alheia.pk)).status_code, 200)
        c_alheio = novo_canal(self.outra, "Z", "")
        self.assertEqual(self.client.post(reverse("precificacao-create"), self.dados(canal=c_alheio.pk)).status_code, 200)
        self.assertFalse(Precificacao.objects.exists())

    def test_formulario_nao_pede_nome_e_aceita_a_mesma_ficha_e_canal_mais_de_uma_vez(self):
        resp = self.client.get(reverse("precificacao-create"))
        self.assertNotIn("nome", resp.context["form"].fields)
        self.assertNotContains(resp, "Nome da precificação")
        self.assertEqual(self.client.post(reverse("precificacao-create"), self.dados()).status_code, 302)
        self.assertEqual(self.client.post(reverse("precificacao-create"), self.dados(percentual_lucro="10")).status_code, 302)
        self.assertEqual(Precificacao.objects.count(), 2)

    def test_lista_filtros_ordenacao_exclusao(self):
        ficha2 = criar_ficha(self.matriz, "Torta", [(self.farinha, "kg", "2")])
        a = nova_precificacao(self.matriz, self.ficha, percentual_lucro=D("10"))  # Bolo, sem canal, preço menor
        b = nova_precificacao(self.matriz, ficha2, canal=self.canal, percentual_lucro=D("90"))  # Torta, iFood, preço maior
        self.assertEqual(self.nomes(), ["Bolo", "Torta"])  # ordenadas pela identificação
        self.assertEqual(self.nomes(sort="preco", dir="desc"), ["Torta", "Bolo"])
        self.assertEqual(self.nomes(sort="ficha", dir="desc"), ["Torta", "Bolo"])
        self.assertEqual(self.nomes(canal=self.canal.pk), ["Torta"])
        self.assertEqual(self.nomes(metodo="markup"), ["Bolo", "Torta"])
        self.assertEqual(self.nomes(metodo="margem"), [])
        self.assertEqual(self.nomes(q="torta"), ["Torta"])  # busca pela ficha
        self.assertEqual(self.nomes(q="ifood"), ["Torta"])  # ... e pelo canal
        self.assertEqual(self.nomes(produto=self.ficha.produto_id), ["Bolo"])
        a.ativo = False
        a.save()
        self.assertEqual(self.nomes(), ["Torta"])
        self.client.post(reverse("precificacao-excluir", args=[b.pk]))
        self.assertFalse(Precificacao.objects.filter(pk=b.pk).exists())

    def test_permissoes_e_tenant(self):
        p = nova_precificacao(self.matriz, self.ficha)
        f_alheia = criar_ficha(self.outra, "Alheia", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        alheia = nova_precificacao(self.outra, f_alheia)
        self.assertEqual(self.client.get(reverse("precificacao-edit", args=[alheia.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("precificacao-excluir", args=[alheia.pk])).status_code, 404)
        self.assertNotIn("Alheia", self.nomes())
        self.client.force_login(usuario("v", self.matriz, ("view",)))
        self.assertEqual(self.client.get(reverse("precificacao-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("precificacao-edit", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("precificacao-excluir", args=[p.pk])).status_code, 403)
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.get(reverse("precificacao")).status_code, 403)

    def test_matriz_alcanca_filial_com_permissao(self):
        f_filial = criar_ficha(self.filial, "FF", [(novo_insumo(self.filial, "I", "kg", "1", 100), "g", "100")])
        da_filial = nova_precificacao(self.filial, f_filial)
        self.assertNotIn("FF", self.nomes())
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change")))
        self.assertIn("FF", self.nomes())
        self.assertEqual(self.client.get(reverse("precificacao-edit", args=[da_filial.pk])).status_code, 200)
        self.client.force_login(usuario("f", self.filial, filiais=("view",)))
        self.assertEqual(self.nomes(), ["FF"])  # filial não vê a matriz

    def test_exportacao(self):
        nova_precificacao(self.matriz, self.ficha, canal=self.canal)
        f_alheia = criar_ficha(self.outra, "Alheia", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        nova_precificacao(self.outra, f_alheia)
        resp = self.client.get(reverse("precificacao"), {"export": "xlsx", "canal": self.canal.pk})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual(linhas[0][:3], ("Produto", "Ficha", "Canal"))  # sem coluna de nome digitado
        self.assertEqual([l[1] for l in linhas[1:]], ["Bolo"])
        self.assertEqual((linhas[1][2], linhas[1][7]), ("IFOOD | Motoboy", "27,79%"))
        self.assertTrue(self.client.get(reverse("precificacao"), {"export": "pdf"}).content.startswith(b"%PDF"))

    def previa(self, **dados):
        return self.client.post(reverse("precificacao-calcular"), {**self.dados(), **dados}).json()

    def test_previa_do_servidor(self):
        d = self.previa()
        r = d["resultado"]
        self.assertEqual((r["custo_ficha"], r["custo_operacional"], r["custo_total"]), ("R$ 10,00", "R$ 2,00", "R$ 12,00"))
        self.assertEqual(d["custo_ficha"], "R$ 10,00")
        self.assertEqual([i["insumo"] for i in d["itens"]], ["Farinha"])
        self.assertTrue(r["canal"].startswith("IFOOD"))
        # a prévia usa a mesma conta da gravação
        self.client.post(reverse("precificacao-create"), self.dados())
        self.assertEqual(r["preco_venda"], Precificacao.objects.get().preco_venda_formatado)

    def test_previa_com_erro_e_sem_ficha(self):
        d = self.previa(percentual_lucro="75")
        self.assertIsNone(d["resultado"])
        self.assertIn("100%", d["erros"]["__all__"])
        self.assertEqual(len(d["itens"]), 1)  # itens da ficha continuam visíveis
        vazio = self.previa(ficha="")
        self.assertEqual((vazio["resultado"], vazio["itens"]), (None, []))

    def test_previa_nao_enxerga_ficha_de_outro_tenant(self):
        f_alheia = criar_ficha(self.outra, "Alheia", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        d = self.previa(ficha=f_alheia.pk)
        self.assertEqual((d["resultado"], d["itens"]), (None, []))

    def test_previa_exige_login_permissao_e_post(self):
        self.assertEqual(self.client.get(reverse("precificacao-calcular")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("precificacao-calcular"), self.dados()).status_code, 302)
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.post(reverse("precificacao-calcular"), self.dados()).status_code, 403)


class CustoOperacionalAutomaticoTests(TestCase):
    """O custo operacional da precificação vem da distribuição por categoria (Custo operacional),
    acompanha as mudanças e preserva o que o usuário ajustou à mão."""

    def setUp(self):
        from apps.categoria.models import Categoria
        from apps.custo.models import CustoOperacional

        self.CustoOperacional = CustoOperacional
        self.matriz = cliente("Matriz", "11222333000181")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        self.ficha = criar_ficha(self.matriz, "Bolo", [(farinha, "kg", "1")])  # categoria "Doces"
        Categoria.objects.filter(cliente=self.matriz, nome="Doces").update(quantidade_vendida=100)
        self.categoria = Categoria.objects.get(cliente=self.matriz, nome="Doces")
        self.canal = novo_canal(self.matriz)

    def dados(self, **extra):
        base = {
            "ficha": self.ficha.pk, "canal": self.canal.pk, "canal_nome": "", "canal_descricao": "", "taxa_percentual": "", "taxa_fixa": "",
            "custo_operacional": "", "metodo_lucro": "valor", "percentual_lucro": "", "valor_lucro": "3,00", "ativo": "on",
        }
        return {**base, **extra}

    def custo(self, valor):  # R$ 300 de custo e uma única categoria com vendas -> R$ 3,00 por unidade
        return self.CustoOperacional.objects.create(cliente=self.matriz, nome="Água+luz+gás", valor=valor)

    def test_campo_em_branco_usa_a_distribuicao_e_nao_e_manual(self):
        self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados())
        p = Precificacao.objects.get()
        self.assertEqual((p.custo_operacional, p.custo_operacional_manual), (300, False))
        self.assertEqual(p.custo_total, 1300)

    def test_valor_igual_ao_automatico_continua_automatico(self):
        self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados(custo_operacional="3,00"))
        self.assertFalse(Precificacao.objects.get().custo_operacional_manual)

    def test_valor_diferente_vira_ajuste_manual(self):
        self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados(custo_operacional="5,00"))
        p = Precificacao.objects.get()
        self.assertEqual((p.custo_operacional, p.custo_operacional_manual), (500, True))

    def test_automatica_acompanha_custos_vendas_e_ajustes_da_categoria(self):
        from apps.categoria.models import Categoria
        from apps.custo.services import ajustar_categoria, restaurar_categoria

        c = self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados())
        p = Precificacao.objects.get()
        c.valor = 50000  # o custo operacional mudou
        c.save()
        p.refresh_from_db()
        self.assertEqual(p.custo_operacional, 500)
        self.categoria.quantidade_vendida = 200  # vendas mudaram: R$ 500 ÷ 200 = R$ 2,50
        self.categoria.save()
        p.refresh_from_db()
        self.assertEqual(p.custo_operacional, 250)
        Categoria.objects.create(cliente=self.matriz, nome="Bebidas")  # absorve o restante do rateio
        ajustar_categoria(None, self.matriz, self.categoria, 20000)  # ajuste da categoria: R$ 200 ÷ 200 = R$ 1,00
        p.refresh_from_db()
        self.assertEqual((p.custo_operacional, p.custo_total, p.custo_operacional_manual), (100, 1100, False))
        restaurar_categoria(None, self.matriz, self.categoria)  # volta ao cálculo automático
        p.refresh_from_db()
        self.assertEqual(p.custo_operacional, 250)
        c.delete()  # sem custos: zero
        p.refresh_from_db()
        self.assertEqual(p.custo_operacional, 0)

    def test_ajuste_manual_da_precificacao_nunca_e_sobrescrito(self):
        c = self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados(custo_operacional="9,99"))
        c.valor = 90000
        c.save()
        self.categoria.quantidade_vendida = 10
        self.categoria.save()
        p = Precificacao.objects.get()
        self.assertEqual((p.custo_operacional, p.custo_operacional_manual), (999, True))

    def test_outra_unidade_nao_e_afetada(self):
        outra = cliente("Outra", "34028316000103")
        farinha = novo_insumo(outra, "Farinha", "kg", "1", 1000)
        ficha = criar_ficha(outra, "Pão", [(farinha, "kg", "1")])
        p = nova_precificacao(outra, ficha, metodo_lucro=METODO_VALOR, percentual_lucro=D("0"), valor_lucro=300)
        self.custo(30000)
        p.refresh_from_db()
        self.assertEqual(p.custo_operacional, 0)

    def test_previa_informa_total_da_categoria_e_se_foi_ajustada(self):
        from apps.custo.services import ajustar_categoria

        self.custo(30000)
        d = self.client.post(reverse("precificacao-calcular"), self.dados()).json()
        s = d["sugestao_custo_operacional"]
        self.assertEqual((s["valor"], s["total_categoria"], s["ajuste_na_categoria"]), ("3,00", "R$ 300,00", False))
        self.assertEqual(d["resultado"]["custo_operacional"], "R$ 3,00")  # em branco: o servidor usa a distribuição
        ajustar_categoria(None, self.matriz, self.categoria, 30000)
        s = self.client.post(reverse("precificacao-calcular"), self.dados()).json()["sugestao_custo_operacional"]
        self.assertTrue(s["ajuste_na_categoria"])

    def test_formulario_de_edicao_marca_o_modo(self):
        self.custo(30000)
        self.client.post(reverse("precificacao-create"), self.dados(custo_operacional="5,00"))
        p = Precificacao.objects.get()
        html = self.client.get(reverse("precificacao-edit", args=[p.pk])).content.decode()
        self.assertIn('data-manual="1"', html)

    def test_falha_de_calculo_numa_precificacao_nao_barra_o_custo(self):
        # margem + taxas >= 100%: a precificação fica como estava e o custo é gravado normalmente
        p = nova_precificacao(self.matriz, self.ficha, self.canal, metodo_lucro=METODO_MARGEM, percentual_lucro=D("50"))
        Precificacao.objects.filter(pk=p.pk).update(percentual_lucro=D("90"))
        self.custo(30000)  # não levanta
        self.assertTrue(self.CustoOperacional.objects.exists())


class ListaPrecificacaoTests(TestCase):
    """Coluna Ficha oculta e alternância tabela/cartões (filtros e paginação juntos)."""

    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        self.canal = novo_canal(self.matriz)
        for n in range(12):
            ficha = criar_ficha(self.matriz, f"Prato {n:02d}", [(self.farinha, "kg", "1")])
            nova_precificacao(self.matriz, ficha, self.canal)

    def cabecalho(self, html):
        return html[html.index("<thead>"):html.index("</thead>")]

    def test_tabela_nao_mostra_a_coluna_ficha_tecnica_mas_mantem_o_vinculo(self):
        html = self.client.get(reverse("precificacao")).content.decode()
        self.assertNotIn("Ficha técnica", self.cabecalho(html))
        self.assertIn("Produto", self.cabecalho(html))
        self.assertTrue(Precificacao.objects.first().ficha_id)  # vínculo preservado no banco

    def test_cartoes_um_por_lancamento_com_preco_e_acoes(self):
        resp = self.client.get(reverse("precificacao"), {"visao": "cartoes", "per_page": "10"})
        html = resp.content.decode()
        self.assertEqual(resp.context["visao"], "cartoes")
        self.assertEqual(html.count('class="card h-100 border"'), 10)
        self.assertNotIn("<thead>", html)
        primeiro = resp.context["precificacoes"][0]
        self.assertIn(primeiro.preco_venda_formatado, html)
        self.assertIn(reverse("precificacao-edit", args=[primeiro.pk]), html)

    def test_visao_invalida_volta_para_tabela(self):
        self.assertEqual(self.client.get(reverse("precificacao"), {"visao": "<x>"}).context["visao"], "tabela")

    def test_filtros_e_paginacao_mantem_o_formato(self):
        html = self.client.get(reverse("precificacao"), {"visao": "cartoes", "per_page": "10", "q": "Prato"}).content.decode()
        self.assertIn("visao=cartoes", html.split("pagination")[1])
        self.assertIn("page=2", html)
        self.assertIn('name="visao" value="cartoes"', html)  # o formulário de filtros preserva o formato

    def test_estado_vazio_nas_duas_visoes(self):
        for visao in ("tabela", "cartoes"):
            self.assertContains(self.client.get(reverse("precificacao"), {"q": "inexistente", "visao": visao}), "Nenhuma precificação encontrada.")

    def test_cartoes_respeitam_o_isolamento_de_unidade(self):
        outra = cliente("Outra", "34028316000103")
        f = criar_ficha(outra, "Segredo", [(novo_insumo(outra, "X", "kg", "1", 100), "g", "100")])
        nova_precificacao(outra, f)
        self.assertNotContains(self.client.get(reverse("precificacao"), {"visao": "cartoes", "per_page": "100"}), "Segredo")
