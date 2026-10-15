"""API de cotações: CRUD de cotação/itens/lojas/preços, vencedor, status, contas a pagar, exportação, tenant e permissões."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.compras.models import ItemCompra
from apps.financeiro.models import CategoriaFinanceira, ContaPagar
from apps.insumo.tests import medida, novo_insumo

from .models import Cotacao, CotacaoPreco
from .tests import D, novo_usuario

V1 = "/api/v1/cotacoes"


class APIBase(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente, self.ua = novo_usuario("a", "11222333000181")
        self.outro, self.ub = novo_usuario("b", "11444777000161")
        self.api_a, self.api_b = self.api(self.ua), self.api(self.ub)
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        self.categoria = CategoriaFinanceira.objects.create(cliente=self.cliente, nome="Insumos", tipo="despesa")

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def post(self, caminho, corpo, codigo=201, api=None):
        r = (api or self.api_a).post(f"{V1}/{caminho}", corpo, format="json")
        self.assertEqual(r.status_code, codigo, r.content[:400])
        return r.json().get("data")

    def cotacao(self, **extra):
        return self.post("", {"titulo": "Outubro", "observacoes": "Obs geral", **extra})

    def montar(self):
        c = self.cotacao()
        item = self.post(f"{c['id']}/itens/", {"insumo": self.farinha.pk, "quantidade": "10", "medida": medida("kg").pk})
        loja = self.post(f"{c['id']}/lojas/", {"nome": "Atacadão", "telefone": "(11) 99999-1234"})
        preco = self.post(f"{c['id']}/precos/", {"item": item["id"], "loja": loja["id"], "marca": "M", "medida": medida("kg").pk, "quantidade": "10",
                                                   "preco_unitario": 1000, "preco_atacado": 800, "qtd_minima_atacado": "10"})
        return c, item, loja, preco


class CrudTests(APIBase):
    def test_cotacao_crud_e_dono_do_usuario(self):
        c = self.cotacao(cliente=self.outro.pk)
        self.assertEqual(Cotacao.objects.get(pk=c["id"]).cliente_id, self.cliente.pk)
        self.assertEqual(c["status"], "rascunho")
        r = self.api_a.patch(f"{V1}/{c['id']}/", {"titulo": "Novo título"}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["titulo"], r.json()["data"]["observacoes"]), (200, "Novo título", "Obs geral"))
        lista = self.api_a.get(f"{V1}/", {"q": "novo"}).json()["data"]["itens"]
        self.assertEqual((lista[0]["id"], lista[0]["qtd_itens"]), (c["id"], 0))
        self.assertEqual(self.api_a.get(f"{V1}/", {"status": "fechada"}).json()["data"]["itens"], [])
        self.assertEqual(self.api_a.delete(f"{V1}/{c['id']}/").status_code, 204)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="excluir").exists())

    def test_criar_importando_a_lista(self):
        ItemCompra.objects.create(cliente=self.cliente, insumo=self.farinha, descricao="Farinha", quantidade=D("3"), medida=medida("kg"))
        c = self.cotacao(importar_lista=True)
        itens = self.api_a.get(f"{V1}/{c['id']}/itens/").json()["data"]
        self.assertEqual([i["descricao"] for i in itens], ["Farinha"])
        importados = self.post(f"{c['id']}/importar-lista/", {}, codigo=200)
        self.assertEqual(importados, [])  # idempotente

    def test_precos_validacao(self):
        c, item, loja, preco = self.montar()
        base = {"item": item["id"], "loja": loja["id"], "medida": medida("kg").pk, "quantidade": "1", "preco_unitario": 100}
        for extra, campo in (({"marca": ""}, "marca"), ({"marca": "M", "preco_atacado": 50}, "qtd_minima_atacado"),
                             ({"marca": "M", "observacao": "x" * 256}, "observacao"), ({"marca": "M", "medida": medida("L").pk}, "medida")):
            r = self.api_a.post(f"{V1}/{c['id']}/precos/", {**base, **extra}, format="json")
            self.assertEqual(r.status_code, 400, extra)
            self.assertIn(campo, r.json()["errors"], extra)
        r = self.api_a.post(f"{V1}/{c['id']}/precos/", {**base, "marca": "M"}, format="json")
        self.assertEqual(r.status_code, 400)  # loja já tem preço para o item
        self.assertEqual(CotacaoPreco.objects.count(), 1)

    def test_patch_e_delete_de_preco_item_e_loja(self):
        c, item, loja, preco = self.montar()
        r = self.api_a.patch(f"{V1}/{c['id']}/precos/{preco['id']}/", {"preco_unitario": 1200}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["preco_unitario"], r.json()["data"]["marca"]), (200, 1200, "M"))
        self.assertEqual(r.json()["data"]["tipo_aplicado"], "atacado")
        self.assertEqual(self.api_a.patch(f"{V1}/{c['id']}/itens/{item['id']}/", {"observacao": "urgente"}, format="json").json()["data"]["observacao"], "urgente")
        self.assertEqual(self.api_a.patch(f"{V1}/{c['id']}/lojas/{loja['id']}/", {"email": "A@B.com"}, format="json").json()["data"]["email"], "a@b.com")
        self.assertEqual(self.api_a.delete(f"{V1}/{c['id']}/precos/{preco['id']}/").status_code, 204)
        self.assertEqual(self.api_a.delete(f"{V1}/{c['id']}/itens/{item['id']}/").status_code, 204)
        self.assertEqual(self.api_a.delete(f"{V1}/{c['id']}/lojas/{loja['id']}/").status_code, 204)

    def test_comparativo(self):
        c, item, loja, preco = self.montar()
        dados = self.api_a.get(f"{V1}/{c['id']}/comparativo/").json()["data"]
        oferta = dados["itens"][0]["ofertas"][0]
        self.assertEqual((oferta["tipo_aplicado"], oferta["preco_aplicado"], oferta["total"], oferta["menor_preco"], oferta["unidade_padrao"]), ("atacado", 800, 8000, True, "KG"))


class FluxoTests(APIBase):
    def test_vencedor_status_contas_e_bloqueios(self):
        c, item, loja, preco = self.montar()
        cid = c["id"]
        self.post(f"{cid}/escolher/", {"preco": preco["id"]}, codigo=200)
        self.post(f"{cid}/status/", {"acao": "enviar"}, codigo=200)
        self.post(f"{cid}/status/", {"acao": "fechar"}, codigo=200)
        # fechada: altera -> 422
        r = self.api_a.post(f"{V1}/{cid}/lojas/", {"nome": "Tardia"}, format="json")
        self.assertEqual(r.status_code, 422, r.content[:300])
        r = self.api_a.patch(f"{V1}/{cid}/", {"titulo": "X"}, format="json")
        self.assertEqual(r.status_code, 422)
        vencimento = (timezone.localdate() + timedelta(days=5)).isoformat()
        contas = self.post(f"{cid}/gerar-contas/", {"categoria": self.categoria.pk, "vencimento": vencimento})
        self.assertEqual((len(contas), contas[0]["valor"], contas[0]["contraparte"]), (1, 8000, "Atacadão"))
        self.post(f"{cid}/gerar-contas/", {"categoria": self.categoria.pk, "vencimento": vencimento}, codigo=200)  # idempotente
        self.assertEqual(ContaPagar.objects.count(), 1)
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/status/", {"acao": "reabrir"}, format="json").status_code, 422)
        self.assertEqual(self.api_a.delete(f"{V1}/{cid}/").status_code, 422)

    def test_validacoes_de_entrada(self):
        c, item, loja, preco = self.montar()
        cid = c["id"]
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/escolher/", {}, format="json").status_code, 400)
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/escolher/", {"preco": 999999}, format="json").status_code, 404)
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/status/", {"acao": "xyz"}, format="json").status_code, 422)
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/status/", {"acao": "fechar"}, format="json").status_code, 422)  # sem vencedor
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/gerar-contas/", {"categoria": self.categoria.pk, "vencimento": "2030-01-01"}, format="json").status_code, 422)  # não fechada
        self.assertEqual(self.api_a.post(f"{V1}/{cid}/gerar-contas/", {"categoria": "x"}, format="json").status_code, 400)

    def test_exportar(self):
        c, *_ = self.montar()
        pdf = self.api_a.get(f"{V1}/{c['id']}/exportar/", {"formato": "pdf"})
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self.api_a.get(f"{V1}/{c['id']}/exportar/", {"formato": "xlsx"}).status_code, 200)
        self.assertEqual(self.api_a.get(f"{V1}/{c['id']}/exportar/", {"formato": "exe"}).status_code, 400)


class TenantEPermissaoTests(APIBase):
    def test_outro_tenant_recebe_404_em_tudo(self):
        c, item, loja, preco = self.montar()
        cid = c["id"]
        for metodo, caminho, corpo in (
            ("get", f"{cid}/", None), ("patch", f"{cid}/", {"titulo": "H"}), ("delete", f"{cid}/", None), ("get", f"{cid}/itens/", None),
            ("post", f"{cid}/itens/", {"descricao": "x", "quantidade": "1", "medida": medida("un").pk}), ("post", f"{cid}/lojas/", {"nome": "x"}),
            ("patch", f"{cid}/precos/{preco['id']}/", {"observacao": "x"}), ("delete", f"{cid}/lojas/{loja['id']}/", None),
            ("post", f"{cid}/escolher/", {"preco": preco["id"]}), ("post", f"{cid}/status/", {"acao": "cancelar"}),
            ("get", f"{cid}/comparativo/", None), ("get", f"{cid}/exportar/", None),
        ):
            r = getattr(self.api_b, metodo)(f"{V1}/{caminho}", corpo, format="json") if corpo is not None else getattr(self.api_b, metodo)(f"{V1}/{caminho}")
            self.assertEqual(r.status_code, 404, (metodo, caminho))
        self.assertEqual(self.api_b.get(f"{V1}/").json()["data"]["itens"], [])
        self.assertEqual((Cotacao.objects.count(), CotacaoPreco.objects.count()), (1, 1))

    def test_loja_ou_item_de_outra_cotacao_e_recusado(self):
        c1, item1, loja1, _ = self.montar()
        c2 = self.cotacao(titulo="Outra")
        r = self.api_a.post(f"{V1}/{c2['id']}/precos/", {"item": item1["id"], "loja": loja1["id"], "marca": "M", "medida": medida("kg").pk, "quantidade": "1", "preco_unitario": 100}, format="json")
        self.assertEqual(r.status_code, 404)  # item não pertence à cotação 2
        loja2 = self.post(f"{c2['id']}/lojas/", {"nome": "L2"})
        item2 = self.post(f"{c2['id']}/itens/", {"descricao": "X", "quantidade": "1", "medida": medida("kg").pk})
        r = self.api_a.post(f"{V1}/{c1['id']}/precos/", {"item": item1["id"], "loja": loja2["id"], "marca": "M", "medida": medida("kg").pk, "quantidade": "1", "preco_unitario": 100}, format="json")
        self.assertEqual(r.status_code, 400)  # loja de outra cotação
        self.assertIn("loja", r.json()["errors"])
        self.assertTrue(item2["id"])

    def test_somente_leitura_nao_escreve_e_anonimo_401(self):
        c, item, loja, preco = self.montar()
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_cotacao"])
        api_ver = self.api(so_ver)
        self.assertEqual(api_ver.get(f"{V1}/").status_code, 200)
        self.assertEqual(api_ver.post(f"{V1}/", {"titulo": "X"}, format="json").status_code, 403)
        self.assertEqual(APIClient().get(f"{V1}/").status_code, 401)
        # usuário só-leitura da MESMA empresa: leitura ok, escrita 403 nas sub-rotas
        so_ver.profile.cliente = self.cliente
        so_ver.profile.save()
        api_ver = self.api(so_ver)
        self.assertEqual(api_ver.get(f"{V1}/{c['id']}/itens/").status_code, 200)
        self.assertEqual(api_ver.post(f"{V1}/{c['id']}/itens/", {"descricao": "x", "quantidade": "1", "medida": medida("un").pk}, format="json").status_code, 403)
        self.assertEqual(api_ver.post(f"{V1}/{c['id']}/escolher/", {"preco": preco["id"]}, format="json").status_code, 403)
        self.assertEqual(api_ver.delete(f"{V1}/{c['id']}/lojas/{loja['id']}/").status_code, 403)
        self.assertEqual(api_ver.get(f"{V1}/{c['id']}/exportar/").status_code, 200)

    def test_gerar_contas_exige_permissao_financeira(self):
        c, item, loja, preco = self.montar()
        self.post(f"{c['id']}/escolher/", {"preco": preco["id"]}, codigo=200)
        self.post(f"{c['id']}/status/", {"acao": "fechar"}, codigo=200)
        from django.contrib.auth.models import Permission

        for grupo in self.ua.groups.all():
            grupo.permissions.remove(*Permission.objects.filter(content_type__app_label="financeiro"))
        r = self.api_a.post(f"{V1}/{c['id']}/gerar-contas/", {"categoria": self.categoria.pk, "vencimento": "2030-01-01"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(ContaPagar.objects.exists())
