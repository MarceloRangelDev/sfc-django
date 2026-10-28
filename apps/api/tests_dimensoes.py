"""Testes do metro em 1, 2 e 3 dimensões (insumo, item de ficha e de processado) e da mão de obra na tela de
Precificação: regra de cálculo, compatibilidade com registros antigos, API (contrato e tenant) e prévias do servidor."""
import json
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse

from apps.ficha.models import Ficha, FichaItem, FichaMaoDeObra
from apps.ficha.services import salvar_ficha
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from apps.maodeobra.models import Colaborador
from apps.precificacao.views import resumo_da_ficha
from apps.processado.recalculo import custo_do_item
from apps.produto.models import Produto
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import medida_e_metro, quantidade_por_dimensoes, sigla_preco_dimensoes

from .tests_cadastros import CadastrosBase, V1

D = Decimal


class RegraDasDimensoesTests(SimpleTestCase):
    def test_area_e_volume_sao_o_produto_com_4_casas(self):
        self.assertEqual(quantidade_por_dimensoes(1, D("2.5")), D("2.5000"))
        self.assertEqual(quantidade_por_dimensoes(2, D("2.4"), D("1.2")), D("2.8800"))
        self.assertEqual(quantidade_por_dimensoes(3, D("2"), D("0.5"), D("0.25")), D("0.2500"))
        self.assertEqual(quantidade_por_dimensoes(2, D("0.3333"), D("0.3333")), D("0.1111"))  # arredonda só no final (0,11108889)

    def test_dimensao_ausente_zero_ou_negativa_e_erro(self):
        for args in ((2, D("1"), None), (2, D("1"), D("0")), (2, D("-1"), D("2")), (3, D("1"), D("1"), None), (4, D("1"))):
            with self.assertRaises(RegraNegocioError, msg=args):
                quantidade_por_dimensoes(*args)

    def test_quantidade_que_arredonda_para_zero_e_erro(self):
        with self.assertRaises(RegraNegocioError):
            quantidade_por_dimensoes(2, D("0.001"), D("0.001"))

    def test_so_o_metro_aceita_dimensoes(self):
        class M:  # medida de mentira: a regra só olha dimensao e fator_base
            def __init__(self, dimensao, fator): self.dimensao, self.fator_base = dimensao, fator
        self.assertTrue(medida_e_metro(M("comprimento", D("1"))))
        self.assertFalse(medida_e_metro(M("comprimento", D("0.01"))))  # centímetro
        self.assertFalse(medida_e_metro(M("area", D("1"))))
        self.assertEqual(sigla_preco_dimensoes(M("comprimento", D("1")), 2), "M²")
        self.assertEqual(sigla_preco_dimensoes(M("comprimento", D("1")), 3), "M³")
        self.assertEqual(sigla_preco_dimensoes(M("comprimento", D("1")), 1), "M")


class MetroNaApiTests(CadastrosBase):
    def insumo(self, **extra):
        corpo = {"nome": "Chapa", "medida": medida("m").pk, "preco_pago": 2880, **extra}
        return self.ok(self.api_a.post(f"{V1}/insumos/", corpo, format="json"), 201)["data"]

    def test_insumo_em_area_calcula_quantidade_e_preco_por_m2(self):
        d = self.insumo(dimensoes=2, comprimento="2.4", largura="1.2")
        self.assertEqual((d["quantidade"], d["dimensoes"], d["rotulo_preco"], d["preco_unidade"]), ("2.8800", 2, "PREÇO (por M²)", 1000))
        self.assertEqual(d["descricao_dimensoes"], "2,4 × 1,2 m = 2,88 m²")

    def test_quantidade_enviada_e_ignorada_com_2_dimensoes(self):
        self.assertEqual(self.insumo(dimensoes=2, comprimento="2", largura="3", quantidade="999")["quantidade"], "6.0000")

    def test_legado_e_outras_unidades_seguem_lineares(self):
        d = self.insumo(quantidade="10")  # sem dimensoes: 1
        self.assertEqual((d["dimensoes"], d["quantidade"], d["comprimento"], d["rotulo_preco"]), (1, "10.0000", None, "PREÇO (por M)"))
        kg = self.ok(self.api_a.post(f"{V1}/insumos/", {"nome": "Sal", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": 100, "dimensoes": 3, "comprimento": "1", "largura": "1", "altura": "1"}, format="json"), 201)["data"]
        self.assertEqual((kg["dimensoes"], kg["comprimento"], kg["quantidade"]), (1, None, "1.0000"))  # fora do metro: ignorado

    def test_validacoes(self):
        for extra in ({"dimensoes": 2, "comprimento": "2"}, {"dimensoes": 3, "comprimento": "1", "largura": "1"}, {"dimensoes": 2, "comprimento": "0.001", "largura": "0.001"}, {"dimensoes": 2, "comprimento": "-1", "largura": "2"}, {"dimensoes": 5}, {}):
            self.erro(self.api_a.post(f"{V1}/insumos/", {"nome": "X", "medida": medida("m").pk, "preco_pago": 100, **extra}, format="json"), 400)

    def test_dimensoes_do_insumo_nao_mudam_quando_ha_receita(self):
        chapa = self.insumo(dimensoes=2, comprimento="2", largura="1")
        self.post("fichas-tecnicas", {"produto": self.produto()["id"], "itens": [{"insumo": chapa["id"], "medida": medida("m").pk, "dimensoes": 2, "comprimento": "1", "largura": "1"}]})
        self.erro(self.api_a.patch(f"{V1}/insumos/{chapa['id']}/", {"dimensoes": 1, "quantidade": "2"}, format="json"), 400)

    def test_item_de_ficha_em_area_custa_proporcional(self):
        chapa = self.insumo(dimensoes=2, comprimento="2.4", largura="1.2")  # R$ 28,80 por 2,88 m² = R$ 10/m²
        ficha = self.post("fichas-tecnicas", {"produto": self.produto()["id"], "itens": [{"insumo": chapa["id"], "medida": medida("m").pk, "dimensoes": 2, "comprimento": "0.5", "largura": "0.3"}]})
        item = ficha["itens"][0]
        self.assertEqual((item["quantidade"], item["dimensoes"], item["custo"], ficha["custo_total"]), ("0.1500", 2, 150, 150))
        self.assertEqual((item["comprimento"], item["largura"], item["altura"]), ("0.5000", "0.3000", None))

    def test_item_precisa_ter_as_mesmas_dimensoes_do_insumo(self):
        chapa = self.insumo(dimensoes=2, comprimento="2", largura="1")
        r = self.api_a.post(f"{V1}/fichas-tecnicas/", {"produto": self.produto()["id"], "itens": [{"insumo": chapa["id"], "medida": medida("m").pk, "quantidade": "1"}]}, format="json")
        self.assertGreaterEqual(r.status_code, 400)  # linear contra área: erro de regra, nunca custo errado
        self.assertFalse(Ficha.objects.exists())

    def test_processado_aceita_itens_em_area(self):
        chapa = self.insumo(dimensoes=2, comprimento="2", largura="1")  # R$ 28,80 por 2 m²
        p = self.post("processados", {"nome": "Painel", "rendimento_quantidade": "1", "rendimento_medida": medida("un").pk,
                                      "itens": [{"insumo": chapa["id"], "medida": medida("m").pk, "dimensoes": 2, "comprimento": "1", "largura": "1"}]})
        self.assertEqual((p["itens"][0]["dimensoes"], p["itens"][0]["quantidade"], p["custo_total"]), (2, "1.0000", 1440))

    def test_tenant_nao_usa_insumo_de_outra_empresa(self):
        chapa_b = Insumo.objects.create(cliente=self.cliente_b, nome="Chapa B", medida=medida("m"), quantidade=D("2"), preco_pago=100, dimensoes=2, comprimento=D("2"), largura=D("1"))
        r = self.api_a.post(f"{V1}/fichas-tecnicas/", {"produto": self.produto()["id"], "itens": [{"insumo": chapa_b.pk, "medida": medida("m").pk, "dimensoes": 2, "comprimento": "1", "largura": "1"}]}, format="json")
        self.assertEqual(r.status_code, 400)


class CustoComDimensoesTests(CadastrosBase):
    def test_custo_do_item_compara_dimensoes(self):
        linear = novo_insumo(self.cliente_a, "Fio", "m", "10", 1000)
        self.assertEqual(custo_do_item(linear, medida("m"), D("2")), 200)
        with self.assertRaises(RegraNegocioError):
            custo_do_item(linear, medida("m"), D("2"), dimensoes=2)

    def test_registro_antigo_continua_linear(self):
        antigo = novo_insumo(self.cliente_a, "Antigo", "g", "100", 400)
        self.assertEqual((antigo.dimensoes, antigo.comprimento), (1, None))
        self.assertEqual(antigo.quantidade_por_extenso, "100 gramas")


class PrecificacaoMaoDeObraTests(CadastrosBase):
    def test_resumo_soma_insumos_e_mao_de_obra_sem_contar_duas_vezes(self):
        colaborador = Colaborador.objects.create(cliente=self.cliente_a, nome="Ana", valor_hora=3000)
        farinha = novo_insumo(self.cliente_a, "Farinha X", "kg", "1", 500)
        ficha = Ficha(cliente=self.cliente_a, produto=Produto.objects.create(cliente=self.cliente_a, nome="Serv", categoria=self.doces_a), nome="F")
        salvar_ficha(None, ficha, [FichaItem(insumo=farinha, medida=medida("kg"), quantidade=D("1"))], mao_de_obra=[FichaMaoDeObra(colaborador=colaborador, horas=D("2"))])
        ficha.refresh_from_db()
        r = resumo_da_ficha(ficha)
        self.assertEqual((r["subtotal_insumos"], r["subtotal_mao_de_obra"]), ("R$ 5,00", "R$ 60,00"))
        self.assertEqual(ficha.custo_total, 6500)  # 500 + 6000: cada parte entra uma vez
        sem = resumo_da_ficha(None)
        self.assertEqual((sem["itens"], sem["mao_de_obra"], sem["subtotal_mao_de_obra"]), ([], [], None))

    def test_ficha_sem_mao_de_obra_mantem_o_comportamento(self):
        ficha = Ficha.objects.get(pk=self.ficha()["id"])
        r = resumo_da_ficha(ficha)
        self.assertEqual((r["mao_de_obra"], r["subtotal_mao_de_obra"], r["subtotal_insumos"]), ([], "R$ 0,00", "R$ 10,00"))


class TelasComDimensoesTests(CadastrosBase):
    """As telas usam as mesmas regras: prévia do servidor e formulário do insumo."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario_a)
        self.chapa = Insumo.objects.create(cliente=self.cliente_a, nome="Chapa", medida=medida("m"), quantidade=D("2.88"), preco_pago=2880, dimensoes=2, comprimento=D("2.4"), largura=D("1.2"))

    def test_previa_da_ficha_calcula_o_produto_no_servidor(self):
        linhas = [{"insumo": self.chapa.pk, "medida": medida("m").pk, "dimensoes": "2", "comprimento": "0,5", "largura": "0,3", "quantidade": "999"},
                  {"insumo": self.chapa.pk, "medida": medida("m").pk, "dimensoes": "2", "comprimento": "0,5", "largura": ""}]
        d = self.client.post(reverse("ficha-calcular"), data=json.dumps({"itens": linhas}), content_type="application/json").json()
        self.assertEqual((d["itens"][0]["custo"], d["itens"][0]["extenso"], d["total"]), ("R$ 1,50", "0,15 m²", "R$ 1,50"))
        self.assertIn("largura", d["itens"][1]["erro"])  # incompleto: erro claro, sem custo

    def test_formulario_do_insumo_grava_dimensoes(self):
        r = self.client.post(reverse("insumo-create"), {"nome": "Madeira", "marca": "", "medida": medida("m").pk, "dimensoes": "3", "comprimento": "2", "largura": "0,5", "altura": "0,1", "preco_pago": "10,00", "ativo": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        i = Insumo.objects.get(nome="Madeira")
        self.assertEqual((i.dimensoes, i.quantidade, i.preco_unidade), (3, D("0.1000"), 10000))

    def test_formulario_aponta_dimensao_faltando(self):
        r = self.client.post(reverse("insumo-create"), {"nome": "Madeira", "medida": medida("m").pk, "dimensoes": "2", "comprimento": "2", "preco_pago": "10,00"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("largura", str(r.context["form"].errors["comprimento"]))


class MedidasAPITests(CadastrosBase):
    def test_medidas_informam_quais_aceitam_dimensoes(self):
        lista = self.ok(self.api_a.get(f"{V1}/medidas/", {"tamanho": 100}))["data"]["itens"]
        por_sigla = {m["sigla"]: m["aceita_dimensoes"] for m in lista}
        self.assertTrue(por_sigla["m"])
        self.assertFalse(por_sigla["cm"] or por_sigla["kg"] or por_sigla["m²"])
