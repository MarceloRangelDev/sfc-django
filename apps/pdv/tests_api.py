"""API do PDV (app móvel): configurações, caixa, vendas idempotentes, comandas, devolução, comprovante, permissões e tenant."""
from decimal import Decimal

from django.core.cache import cache
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.estoque.services import saldo_do_insumo
from apps.pdvconfig.models import FormaPagamento, Mesa

from .models import Venda
from .tests import PdvBase

V1 = "/api/v1"
D = Decimal


class APIPdvBase(PdvBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.api_o, self.api_g, self.api_b = self.api(self.operador), self.api(self.gestor), self.api(self.gestor_b)

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def ok(self, resposta, codigo=200):
        self.assertEqual(resposta.status_code, codigo, resposta.content[:500])
        return resposta.json().get("data")

    def abrir(self, api=None, valor=10000):
        return self.ok((api or self.api_o).post(f"{V1}/pdv/caixa/abrir/", {"valor_abertura": valor}, format="json"), 201)

    def corpo(self, **extra):
        return {"itens": [{"produto": self.bolo.pk, "quantidade": 2}], "pagamentos": [{"forma": self.dinheiro.pk, "valor": 1000, "valor_recebido": 1500}], **extra}


class ConfiguracaoAPITests(APIPdvBase):
    def test_forma_de_pagamento_com_os_tres_modos(self):
        for corpo in ({"nome": "A", "tipo": "debito", "taxa_modo": "percentual", "taxa_percentual": "1.99"},
                      {"nome": "B", "tipo": "debito", "taxa_modo": "fixa", "taxa_fixa": 40},
                      {"nome": "C", "tipo": "credito", "taxa_modo": "ambas", "taxa_percentual": "3", "taxa_fixa": 25}):
            self.ok(self.api_g.post(f"{V1}/formas-pagamento/", corpo, format="json"), 201)
        formas = {f.nome: f for f in FormaPagamento.objects.filter(nome__in="ABC")}
        self.assertEqual((formas["A"].taxa_fixa, formas["B"].taxa_percentual, formas["C"].calcular_taxa(10000)), (0, D("0"), 325))
        r = self.api_g.post(f"{V1}/formas-pagamento/", {"nome": "D", "tipo": "pix", "taxa_modo": "fixa"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("taxa_fixa", r.json()["errors"])
        self.assertEqual(self.api_g.post(f"{V1}/formas-pagamento/", {"nome": "E", "tipo": "pix", "taxa_modo": "bola"}, format="json").status_code, 400)

    def test_operador_nao_gerencia_formas_nem_mesas_e_tenant(self):
        self.assertEqual(self.api_o.post(f"{V1}/formas-pagamento/", {"nome": "X", "tipo": "pix"}, format="json").status_code, 403)
        self.assertEqual(self.api_o.post(f"{V1}/mesas/", {"nome": "M"}, format="json").status_code, 403)
        mesa = self.ok(self.api_g.post(f"{V1}/mesas/", {"nome": "Mesa 9"}, format="json"), 201)
        self.assertEqual(self.api_b.get(f"{V1}/mesas/{mesa['id']}/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/mesas/").json()["data"]["itens"], [])
        self.assertEqual(self.api_b.get(f"{V1}/formas-pagamento/{self.pix.pk}/").status_code, 404)
        lista = self.ok(self.api_g.get(f"{V1}/formas-pagamento/"))["itens"]
        self.assertEqual({f["nome"] for f in lista}, {"Dinheiro", "PIX", "Crédito"})
        self.assertEqual(self.api_g.delete(f"{V1}/formas-pagamento/{self.pix.pk}/").status_code, 403)  # Gestor ver/criar/editar; excluir é do Administrador

    def test_configuracao_do_pdv(self):
        self.assertTrue(self.ok(self.api_g.get(f"{V1}/pdv/configuracao/"))["controla_estoque"])
        dados = self.ok(self.api_g.patch(f"{V1}/pdv/configuracao/", {"controla_estoque": False, "formato_comprovante": "termica58"}, format="json"))
        self.assertEqual((dados["controla_estoque"], dados["formato_comprovante"]), (False, "termica58"))
        self.assertEqual(self.api_g.patch(f"{V1}/pdv/configuracao/", {"formato_comprovante": "xyz"}, format="json").status_code, 400)
        self.assertEqual(self.api_o.get(f"{V1}/pdv/configuracao/").status_code, 403)
        self.assertEqual(APIClient().get(f"{V1}/pdv/configuracao/").status_code, 401)


class VendaAPITests(APIPdvBase):
    def test_fluxo_completo_com_idempotencia_por_cabecalho(self):
        self.assertEqual(self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json").status_code, 400)  # sem caixa
        self.abrir()
        produtos = self.ok(self.api_o.get(f"{V1}/pdv/produtos/"))["itens"]
        self.assertEqual([(p["nome"], p["preco"]) for p in produtos], [("Bolo", 500)])
        r1 = self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json", HTTP_IDEMPOTENCY_KEY="app-001")
        d1 = self.ok(r1, 201)
        self.assertEqual((d1["total"], d1["troco"], d1["status"], d1["criada"]), (1000, 500, "paga", True))
        r2 = self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json", HTTP_IDEMPOTENCY_KEY="app-001")
        d2 = self.ok(r2, 200)
        self.assertEqual((d2["id"], d2["criada"]), (d1["id"], False))
        self.assertEqual((Venda.objects.filter(status="paga").count(), saldo_do_insumo(self.farinha)), (1, D("9.5")))

    def test_validacoes_do_corpo(self):
        self.abrir()
        casos = [
            self.corpo(itens=[]), self.corpo(pagamentos=[]), self.corpo(pagamentos=[{"forma": self.dinheiro.pk, "valor": 999}]),
            self.corpo(itens=[{"produto": self.bolo.pk, "quantidade": 0}]), self.corpo(desconto=100), self.corpo(consumidor=999999),
            {"itens": "x", "pagamentos": "y"},
        ]
        for corpo in casos:
            r = self.api_o.post(f"{V1}/pdv/vendas/", corpo, format="json")
            self.assertIn(r.status_code, (400, 422), corpo)
        self.assertFalse(Venda.objects.exists())

    def test_preco_do_cliente_e_ignorado_e_gestor_da_desconto(self):
        self.abrir(self.api_g, 0)
        corpo = {"itens": [{"produto": self.bolo.pk, "quantidade": 2, "preco": 1}], "pagamentos": [{"forma": self.pix.pk, "valor": 900}], "desconto": 100}
        # Mesmo o gestor (que tem a permissão) precisa informar a credencial: sem `autorizacao`, 422 e nada é gravado.
        self.assertEqual(self.api_g.post(f"{V1}/pdv/vendas/", corpo, format="json").status_code, 422)
        self.assertFalse(Venda.objects.exists())
        d = self.ok(self.api_g.post(f"{V1}/pdv/vendas/", {**corpo, "autorizacao": {"senha": "SenhaForte123!"}}, format="json"), 201)
        self.assertEqual((d["subtotal"], d["desconto"], d["total"]), (1000, 100, 900))

    def test_visibilidade_cancelamento_e_devolucao(self):
        self.abrir()
        venda = self.ok(self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json"), 201)
        item = venda["itens"][0]
        # Cancelar exige a credencial de quem tem pdv.cancelar_venda (o operador sozinho não basta, nem com a própria senha).
        self.assertEqual(self.api_o.post(f"{V1}/pdv/vendas/{venda['id']}/cancelar/", {"motivo": "x"}, format="json").status_code, 422)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/vendas/{venda['id']}/cancelar/", {"motivo": "x", "autorizacao": {"senha": "SenhaForte123!"}}, format="json").status_code, 422)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/vendas/{venda['id']}/itens/{item['id']}/devolver/", {}, format="json").status_code, 403)
        self.assertEqual(self.api_b.get(f"{V1}/pdv/vendas/{venda['id']}/").status_code, 404)
        self.assertEqual(self.api_b.post(f"{V1}/pdv/vendas/{venda['id']}/cancelar/", {"motivo": "x"}, format="json").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/pdv/vendas/").json()["data"]["itens"], [])
        # devolução pelo gestor (em PIX, sem precisar de caixa) repondo o estoque
        d = self.ok(self.api_g.post(f"{V1}/pdv/vendas/{venda['id']}/itens/{item['id']}/devolver/", {"quantidade": "1", "forma_estorno": self.pix.pk, "motivo": "queimado", "repor_estoque": True}, format="json"), 201)
        self.assertEqual((d["valor"], d["estoque_reposto"]), (500, True))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.75"))
        r = self.api_g.post(f"{V1}/pdv/vendas/{venda['id']}/itens/{item['id']}/devolver/", {"quantidade": "5", "forma_estorno": self.pix.pk, "motivo": "x"}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.api_g.post(f"{V1}/pdv/vendas/{venda['id']}/itens/999999/devolver/", {"quantidade": "1", "forma_estorno": self.pix.pk, "motivo": "x"}, format="json").status_code, 404)

    def test_cancelar_pelo_gestor_e_lista_filtrada(self):
        self.abrir()
        venda = self.ok(self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json"), 201)
        self.ok(self.api_g.post(f"{V1}/pdv/vendas/{venda['id']}/cancelar/", {"motivo": "teste", "autorizacao": {"senha": "SenhaForte123!"}}, format="json"))
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))
        self.assertEqual(len(self.ok(self.api_g.get(f"{V1}/pdv/vendas/", {"status": "cancelada"}))["itens"]), 1)
        self.assertEqual(len(self.ok(self.api_o.get(f"{V1}/pdv/vendas/", {"q": str(venda["numero"])}))["itens"]), 1)
        self.assertEqual(self.api_o.post(f"{V1}/pdv/vendas/{venda['id']}/cancelar/", {"motivo": "de novo"}, format="json").status_code, 422)

    def test_comprovante_em_json_pdf_e_escpos(self):
        self.abrir()
        venda = self.ok(self.api_o.post(f"{V1}/pdv/vendas/", self.corpo(), format="json"), 201)
        url = f"{V1}/pdv/vendas/{venda['id']}/comprovante/"
        dados = self.ok(self.api_o.get(url, {"tamanho": "termica58"}))
        self.assertIn("COMPROVANTE NAO FISCAL", "\n".join(dados["linhas"]))
        self.assertTrue(all(len(l) <= 32 for l in dados["linhas"]))
        self.assertTrue(self.api_o.get(url, {"formato": "pdf"}).content.startswith(b"%PDF"))
        self.assertTrue(self.api_o.get(url, {"formato": "escpos"}).content.startswith(b"\x1b@"))
        self.assertEqual(self.api_o.get(url, {"formato": "exe"}).status_code, 400)
        self.assertEqual(self.api_b.get(url).status_code, 404)


class CaixaEComandaAPITests(APIPdvBase):
    def test_caixa_sangria_fechamento_e_conferencia(self):
        caixa = self.abrir()
        self.assertEqual(self.ok(self.api_o.get(f"{V1}/pdv/caixa/atual/"))["id"], caixa["id"])
        self.assertEqual(self.api_o.post(f"{V1}/pdv/caixa/abrir/", {}, format="json").status_code, 422)  # já aberto
        self.assertEqual(self.api_o.post(f"{V1}/pdv/caixa/sangria/", {"valor": 100, "motivo": "x"}, format="json").status_code, 403)
        self.abrir(self.api_g, 5000)
        self.ok(self.api_g.post(f"{V1}/pdv/caixa/sangria/", {"valor": 1000, "motivo": "cofre"}, format="json"), 201)
        self.ok(self.api_g.post(f"{V1}/pdv/caixa/suprimento/", {"valor": 200, "motivo": "troco"}, format="json"), 201)
        self.assertEqual(self.api_g.post(f"{V1}/pdv/caixa/sangria/", {"valor": 10**7, "motivo": "x"}, format="json").status_code, 422)
        fechado = self.ok(self.api_o.post(f"{V1}/pdv/caixa/fechar/", {"valor_contado": 9900}, format="json"))
        self.assertEqual((fechado["status"], fechado["valor_esperado"], fechado["diferenca"]), ("fechado", 10000, -100))
        self.assertEqual(self.api_o.post(f"{V1}/pdv/caixa/{caixa['id']}/conferir/", {}, format="json").status_code, 403)
        self.ok(self.api_g.post(f"{V1}/pdv/caixa/{caixa['id']}/conferir/", {"observacao": "ok"}, format="json"))
        self.assertTrue(self.ok(self.api_g.get(f"{V1}/pdv/caixa/{caixa['id']}/"))["conferido"])
        self.assertEqual(self.api_b.get(f"{V1}/pdv/caixa/{caixa['id']}/").status_code, 404)
        self.assertEqual(self.api_b.post(f"{V1}/pdv/caixa/{caixa['id']}/conferir/", {}, format="json").status_code, 404)

    def test_comanda_com_conta_dividida_e_idempotencia(self):
        self.abrir()
        mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 2")
        comanda = self.ok(self.api_o.post(f"{V1}/pdv/comandas/", {"mesa": mesa.pk}, format="json"), 201)
        cid = comanda["id"]
        self.assertEqual(self.api_o.post(f"{V1}/pdv/comandas/", {"mesa": mesa.pk}, format="json").status_code, 422)  # mesa ocupada
        self.ok(self.api_o.post(f"{V1}/pdv/comandas/{cid}/itens/", {"produto": self.bolo.pk, "quantidade": 3}, format="json"))
        detalhe = self.ok(self.api_o.get(f"{V1}/pdv/comandas/{cid}/"))
        self.assertEqual(detalhe["subtotal"], 1500)
        pagamentos = [{"forma": self.pix.pk, "valor": 750, "pagador": "A"}, {"forma": self.dinheiro.pk, "valor": 750, "pagador": "B"}]
        pago = self.ok(self.api_o.post(f"{V1}/pdv/comandas/{cid}/pagar/", {"pagamentos": pagamentos}, format="json", HTTP_IDEMPOTENCY_KEY="c-1"), 201)
        self.assertEqual((pago["status"], len(pago["pagamentos"])), ("paga", 2))
        repetido = self.api_o.post(f"{V1}/pdv/comandas/{cid}/pagar/", {"pagamentos": pagamentos}, format="json", HTTP_IDEMPOTENCY_KEY="c-1")
        self.assertEqual((repetido.status_code, repetido.json()["data"]["criada"]), (200, False))
        self.assertEqual(self.api_o.post(f"{V1}/pdv/comandas/{cid}/itens/", {"produto": self.bolo.pk, "quantidade": 1}, format="json").status_code, 422)
        self.assertEqual(self.api_b.get(f"{V1}/pdv/comandas/{cid}/").status_code, 404)

    def test_remover_item_e_cancelar_comanda(self):
        self.abrir()
        mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 3")
        cid = self.ok(self.api_o.post(f"{V1}/pdv/comandas/", {"mesa": mesa.pk}, format="json"), 201)["id"]
        d = self.ok(self.api_o.post(f"{V1}/pdv/comandas/{cid}/itens/", {"produto": self.bolo.pk, "quantidade": 1}, format="json"))
        self.ok(self.api_o.delete(f"{V1}/pdv/comandas/{cid}/itens/{d['itens'][0]['id']}/"))
        self.assertEqual(self.ok(self.api_o.get(f"{V1}/pdv/comandas/{cid}/"))["subtotal"], 0)
        self.assertEqual(len(self.ok(self.api_o.get(f"{V1}/pdv/comandas/"))), 1)
        self.ok(self.api_g.post(f"{V1}/pdv/vendas/{cid}/cancelar/", {"motivo": "saiu", "autorizacao": {"senha": "SenhaForte123!"}}, format="json"))
        self.assertEqual(self.ok(self.api_o.get(f"{V1}/pdv/comandas/")), [])
