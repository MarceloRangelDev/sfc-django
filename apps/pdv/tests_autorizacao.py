"""Autorização por senha OU código de crachá no PDV: desconto (tela e API), limpar, cancelar, oferta fiscal e estoque visível.

Cobre: credencial validada e auditada no servidor (sem vazar senha/código), escopo da unidade (supervisor de outro tenant não autoriza),
desconto sempre exigindo credencial (mesmo para quem tem a permissão) e isolamento multi-tenant dos endpoints novos."""
import json
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.estoque.models import SaldoProduto
from apps.fiscal.models import ConfiguracaoFiscal, DocumentoFiscal
from web_project.testing import habilitar_modulo

from . import autorizacao
from .models import CodigoAutorizacao, Venda
from .tests import PdvBase

D = Decimal
SENHA = "SenhaForte123!"
V1 = "/api/v1"


class AutorizacaoBase(PdvBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def post_json(self, nome, corpo, **kw):
        return self.client.post(reverse(nome, **kw), json.dumps(corpo), content_type="application/json")

    def finalizar(self, **extra):
        corpo = {"itens": [{"produto": self.bolo.pk, "quantidade": 2}], "pagamentos": [{"forma": self.dinheiro.pk, "valor": 900}], "desconto": 100, **extra}
        return self.post_json("pdv-api-finalizar", corpo)

    def trilha(self):
        return " ".join(RegistroAuditoria.objects.filter(modulo="pdv").values_list("descricao", flat=True))


class CodigoDeCrachaTests(AutorizacaoBase):
    def test_codigo_autoriza_e_so_o_hash_e_guardado(self):
        codigo = autorizacao.gerar_codigo(self.gestor)
        registro = CodigoAutorizacao.objects.get(usuario=self.gestor)
        self.assertNotIn(codigo, registro.codigo_hash)
        self.assertEqual(len(registro.codigo_hash), 64)
        quem = autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=codigo)
        self.assertEqual(quem, self.gestor)
        self.assertEqual(autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=codigo.lower()), self.gestor)  # leitor pode trocar caixa

    def test_codigo_errado_gerar_outro_invalida_o_anterior(self):
        antigo = autorizacao.gerar_codigo(self.gestor)
        novo = autorizacao.gerar_codigo(self.gestor)
        self.assertNotEqual(antigo, novo)
        for invalido in (antigo, "ZZZ", "   "):
            with self.assertRaises(Exception):
                autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=invalido)
        self.assertEqual(autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=novo), self.gestor)

    def test_codigo_de_supervisor_de_outra_unidade_ou_sem_permissao_nao_autoriza(self):
        de_b = autorizacao.gerar_codigo(self.gestor_b)
        with self.assertRaises(Exception):
            autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=de_b)  # outro tenant
        do_operador = autorizacao.gerar_codigo(self.operador)
        with self.assertRaises(Exception):
            autorizacao.autorizar(self.req_o, "pdv.conceder_desconto", self.cliente, codigo=do_operador)  # sem a permissão

    def test_endpoint_de_geracao_so_para_quem_autoriza(self):
        self.client.force_login(self.operador)
        self.assertEqual(self.client.post(reverse("pdv-api-codigo-autorizacao")).status_code, 403)
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("pdv-api-codigo-autorizacao"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=r.json()["codigo"]), self.gestor)
        self.assertEqual(self.client.get(reverse("pdv-api-codigo-autorizacao")).status_code, 405)

    def test_tentativas_com_codigo_errado_contam_no_bloqueio(self):
        for _ in range(5):
            with self.assertRaises(Exception):
                autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo="ERRADO")
        certo = autorizacao.gerar_codigo(self.gestor)
        with self.assertRaises(Exception):
            autorizacao.autorizar(self.req_o, "pdv.cancelar_venda", self.cliente, codigo=certo)  # bloqueado


class DescontoAutorizadoTests(AutorizacaoBase):
    def setUp(self):
        super().setUp()
        self.abrir()
        self.client.force_login(self.operador)

    def test_sem_credencial_nao_vende_com_desconto(self):
        r = self.finalizar(chave="d1")
        self.assertEqual(r.status_code, 422)
        self.assertIn("autorização", r.json()["erro"])
        self.assertFalse(Venda.objects.exists())

    def test_senha_de_supervisor_concede_e_audita_sem_vazar_credencial(self):
        r = self.finalizar(chave="d2", autorizacao={"usuario": "gestor", "senha": SENHA})
        self.assertEqual(r.status_code, 201, r.content)
        venda = Venda.objects.get()
        self.assertEqual((venda.subtotal, venda.desconto, venda.total), (1000, 100, 900))
        self.assertIn("conceder desconto de R$ 1,00", self.trilha())
        self.assertIn("Autorizado por gestor", self.trilha())
        self.assertNotIn(SENHA, self.trilha())

    def test_codigo_do_cracha_concede_desconto(self):
        codigo = autorizacao.gerar_codigo(self.gestor)
        r = self.finalizar(chave="d3", autorizacao={"codigo": codigo})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn(codigo, self.trilha())

    def test_quem_nao_pode_conceder_ou_e_de_outra_unidade_e_recusado(self):
        # o operador não tem conceder_desconto: a própria senha não basta
        self.assertEqual(self.finalizar(chave="d4", autorizacao={"senha": SENHA}).status_code, 422)
        # supervisor de outro tenant
        self.assertEqual(self.finalizar(chave="d5", autorizacao={"usuario": "gestor_b", "senha": SENHA}).status_code, 422)
        self.assertEqual(self.finalizar(chave="d6", autorizacao={"usuario": "gestor", "senha": "errada"}).status_code, 422)
        self.assertFalse(Venda.objects.exists())

    def test_sem_desconto_nao_pede_autorizacao(self):
        r = self.finalizar(chave="d7", desconto=0, pagamentos=[{"forma": self.dinheiro.pk, "valor": 1000}])
        self.assertEqual(r.status_code, 201)

    def test_mesmo_quem_tem_a_permissao_precisa_da_credencial(self):
        self.client.force_login(self.gestor)
        self.abrir(self.gestor)
        self.assertEqual(self.finalizar(chave="d8").status_code, 422)
        self.assertEqual(self.finalizar(chave="d8", autorizacao={"senha": SENHA}).status_code, 201)


class ComandaDescontoTests(AutorizacaoBase):
    def test_pagar_comanda_com_desconto_exige_credencial(self):
        from apps.pdv import services as s
        from apps.pdvconfig.models import Mesa

        caixa = self.abrir()
        venda = s.abrir_comanda(self.req_o, caixa, Mesa.objects.create(cliente=self.cliente, nome="M1"))
        s.adicionar_item(self.req_o, venda, self.bolo.pk, 2)
        self.client.force_login(self.operador)
        corpo = {"pagamentos": [{"forma": self.dinheiro.pk, "valor": 900}], "desconto": 100, "chave": "c1"}
        self.assertEqual(self.post_json("pdv-comanda-pagar", corpo, args=[venda.pk]).status_code, 422)
        r = self.post_json("pdv-comanda-pagar", {**corpo, "autorizacao": {"usuario": "gestor", "senha": SENHA}}, args=[venda.pk])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Venda.objects.get(pk=venda.pk).desconto, 100)


class LimparECancelarTests(AutorizacaoBase):
    def test_limpar_exige_credencial_e_audita_o_resumo(self):
        self.abrir()
        self.client.force_login(self.operador)
        corpo = {"acao": "limpar", "resumo": "2 item(ns), total R$ 10,00"}
        self.assertEqual(self.post_json("pdv-api-autorizar", {**corpo, "senha": "errada"}).status_code, 403)
        self.assertEqual(self.post_json("pdv-api-autorizar", {**corpo, "usuario": "gestor", "senha": SENHA}).status_code, 200)
        self.assertIn("limpar a venda em andamento (2 item(ns), total R$ 10,00)", self.trilha())
        codigo = autorizacao.gerar_codigo(self.gestor)
        self.assertEqual(self.post_json("pdv-api-autorizar", {**corpo, "codigo": codigo}).status_code, 200)
        self.assertEqual(self.post_json("pdv-api-autorizar", {**corpo, "usuario": "gestor_b", "senha": SENHA}).status_code, 403)

    def test_cancelar_pela_tela_com_codigo(self):
        venda = self.vender(self.abrir())[0]
        self.client.force_login(self.operador)
        codigo = autorizacao.gerar_codigo(self.gestor)
        self.client.post(reverse("pdv-venda-cancelar", args=[venda.pk]), {"motivo": "teste", "codigo_autorizacao": codigo})
        self.assertEqual(Venda.objects.get(pk=venda.pk).status, "cancelada")
        self.assertNotIn(codigo, self.trilha())


class NotaFiscalNoPdvTests(AutorizacaoBase):
    def preparar(self, emite=True):
        for c in (self.cliente, self.outro):
            habilitar_modulo(c, "fiscal")
        self.cliente.emite_nota_fiscal = emite
        self.cliente.save(update_fields=["emite_nota_fiscal"])
        config = ConfiguracaoFiscal.da_unidade(self.cliente)
        config.habilita_nfce = True
        config.save()
        self.abrir(self.gestor)
        self.client.force_login(self.gestor)

    def venda_json(self):
        corpo = {"itens": [{"produto": self.bolo.pk, "quantidade": 2}], "pagamentos": [{"forma": self.dinheiro.pk, "valor": 1000}], "chave": "n1"}
        return self.post_json("pdv-api-finalizar", corpo).json()

    def test_oferece_nota_e_a_emissao_e_idempotente(self):
        self.preparar()
        dados = self.venda_json()
        self.assertEqual(dados["nota_fiscal"]["tipos"], ["nfce"])
        url = dados["nota_fiscal"]["url"]
        r = self.client.post(url, json.dumps({"tipo": "nfce"}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["criado"])
        r2 = self.client.post(url, json.dumps({"tipo": "nfce"}), content_type="application/json")
        self.assertFalse(r2.json()["criado"])
        self.assertEqual(DocumentoFiscal.objects.filter(venda_id=dados["venda"]).count(), 1)
        self.assertNotEqual(r.json()["status"], "autorizada")  # sem provedor, nunca simula autorização

    def test_nao_pergunta_quando_a_empresa_nao_emite_nem_para_operador(self):
        self.preparar(emite=False)
        self.assertIsNone(self.venda_json()["nota_fiscal"])

    def test_nota_de_outra_unidade_e_404_e_tipo_invalido_422(self):
        self.preparar()
        dados = self.venda_json()
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.post(dados["nota_fiscal"]["url"], json.dumps({"tipo": "nfce"}), content_type="application/json").status_code, 404)
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.post(dados["nota_fiscal"]["url"], json.dumps({"tipo": "xyz"}), content_type="application/json").status_code, 422)


class EstoqueVisivelTests(AutorizacaoBase):
    def test_json_mostra_saldo_ou_nao_controlado(self):
        self.abrir()
        self.client.force_login(self.operador)
        item = self.client.get(reverse("pdv-api-produtos")).json()["itens"][0]
        self.assertEqual(item["estoque"], {"controlado": False, "saldo": None, "rotulo": "Estoque não controlado"})
        self.bolo.controla_estoque = True
        self.bolo.save(update_fields=["controla_estoque"])
        item = self.client.get(reverse("pdv-api-produtos")).json()["itens"][0]
        self.assertEqual(item["estoque"], {"controlado": True, "saldo": "0", "rotulo": "0 em estoque"})  # controla, mas sem movimento
        SaldoProduto.objects.create(cliente=self.cliente, produto=self.bolo, saldo=D("7.500"))
        item = self.client.get(reverse("pdv-api-produtos")).json()["itens"][0]
        self.assertEqual(item["estoque"]["saldo"], "7.5")

    def test_comanda_mostra_estoque_do_produto(self):
        from apps.pdv import services as s
        from apps.pdvconfig.models import Mesa

        caixa = self.abrir()
        self.bolo.controla_estoque = True
        self.bolo.save(update_fields=["controla_estoque"])
        SaldoProduto.objects.create(cliente=self.cliente, produto=self.bolo, saldo=D("12"))
        venda = s.abrir_comanda(self.req_o, caixa, Mesa.objects.create(cliente=self.cliente, nome="M2"))
        self.client.force_login(self.operador)
        self.assertContains(self.client.get(reverse("pdv-comanda", args=[venda.pk])), "12 em estoque")

    def test_saldo_de_outra_unidade_nao_aparece(self):
        self.abrir()
        self.client.force_login(self.gestor_b)
        r = self.client.get(reverse("pdv-api-produtos"))
        self.assertEqual(r.status_code, 409)  # sem caixa na unidade B: nada do catálogo/saldo de A


class ApiAutorizacaoTests(AutorizacaoBase):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o = APIClient(), APIClient()
        for api, usuario in ((self.api_g, self.gestor), (self.api_o, self.operador)):
            api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")

    def test_validar_limpar_e_gerar_codigo_pela_api(self):
        self.assertEqual(self.api_o.post(f"{V1}/pdv/autorizacoes/validar/", {"acao": "limpar", "usuario": "gestor", "senha": SENHA}, format="json").status_code, 200)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/autorizacoes/validar/", {"acao": "limpar", "senha": "x"}, format="json").status_code, 422)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/autorizacoes/validar/", {"acao": "nada"}, format="json").status_code, 400)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/autorizacoes/codigo/", {}, format="json").status_code, 403)
        codigo = self.api_g.post(f"{V1}/pdv/autorizacoes/codigo/", {}, format="json").json()["data"]["codigo"]
        self.assertEqual(self.api_o.post(f"{V1}/pdv/autorizacoes/validar/", {"acao": "limpar", "codigo": codigo}, format="json").status_code, 200)

    def test_cancelar_pela_api_com_credencial_de_quem_pode(self):
        venda = self.vender(self.abrir())[0]
        url = f"{V1}/pdv/vendas/{venda.pk}/cancelar/"
        self.assertEqual(self.api_o.post(url, {"motivo": "x"}, format="json").status_code, 422)
        r = self.api_o.post(url, {"motivo": "teste", "autorizacao": {"usuario": "gestor", "senha": SENHA}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Venda.objects.get(pk=venda.pk).status, "cancelada")
        self.assertIn("Autorizado por gestor", self.trilha())

    def test_listagem_da_venda_informa_a_entrega(self):
        venda = self.vender(self.abrir())[0]
        dados = self.api_g.get(f"{V1}/pdv/vendas/{venda.pk}/").json()["data"]
        self.assertEqual(dados["entrega"], "")
