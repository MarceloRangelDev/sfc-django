"""Pedidos e cozinha: regra de confirmação, baixa de estoque na confirmação (sem duplicar), fila, transições, cancelamento,
integração com o PDV, permissões ("qualquer um move"), tenant, idempotência e API."""
import json
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.cache import cache
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.estoque.models import MovimentoEstoque
from apps.estoque.services import saldo_do_insumo
from apps.pdv import services as pdv
from apps.pdv.models import Venda, VendaItem
from apps.pdv.tests import PdvBase, Req
from apps.pdvconfig.models import ConfiguracaoPdv, Mesa
from web_project.historico import HistoricoImutavelError
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import services as s
from .models import Pedido, PedidoEvento

D = Decimal
V1 = "/api/v1"


class PedidoBase(PdvBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        for c in (self.cliente, self.outro):
            habilitar_modulo(c, "pedido")
        self.caixa = self.abrir()
        self.mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 5")
        self.config = ConfiguracaoPdv.da_unidade(self.cliente)

    def regra(self, valor):
        self.config.confirmacao_pedido = valor
        self.config.save()

    def comanda(self, qtd=2):
        venda = pdv.abrir_comanda(self.req_o, self.caixa, self.mesa)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, qtd)
        return Venda.objects.get(pk=venda.pk)

    def pedido(self, regra="manual", **kw):
        self.regra(regra)
        venda = self.comanda()
        pedido, _ = s.criar_pedido(self.req_o, venda, tipo="mesa", **kw)
        return pedido


class RegraDeConfirmacaoTests(PedidoBase):
    def test_manual_fica_novo_sem_baixar_estoque(self):
        p = self.pedido("manual")
        self.assertEqual(p.status, "novo")
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))
        s.confirmar(self.req_o, p)
        p.refresh_from_db()
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("confirmado", D("9.5")))
        self.assertIsNotNone(p.confirmado_em)

    def test_lancado_entra_direto_na_fila_e_baixa(self):
        p = self.pedido("lancado")
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("confirmado", D("9.5")))
        self.assertEqual([e.para_status for e in p.eventos.all()], ["novo", "confirmado"])

    def test_pagamento_confirma_so_ao_receber(self):
        p = self.pedido("pagamento")
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("novo", D("10")))
        pdv.pagar_comanda(self.req_o, p.venda, [{"forma": self.pix.pk, "valor": 1000}])
        p.refresh_from_db()
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("confirmado", D("9.5")))  # baixa UMA vez, mesmo com o recebimento

    def test_pedido_de_venda_ja_paga_com_regra_pagamento_confirma_na_criacao(self):
        self.regra("pagamento")
        venda, _ = self.vender(self.caixa, qtd=2)
        p, _ = s.criar_pedido(self.req_o, venda, tipo="balcao")
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("confirmado", D("9.5")))  # já baixado na venda: não baixa de novo

    def test_confirmar_duas_vezes_e_recusado_sem_baixar_de_novo(self):
        p = self.pedido("manual")
        s.confirmar(self.req_o, p)
        with self.assertRaises(RegraNegocioError):
            s.confirmar(self.req_o, p)
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))

    def test_estoque_insuficiente_impede_confirmar(self):
        venda = self.comanda(qtd=60)  # precisa de 15 kg; há 10
        p, _ = s.criar_pedido(self.req_o, venda)
        with self.assertRaises(RegraNegocioError):
            s.confirmar(self.req_o, p)
        p.refresh_from_db()
        self.assertEqual((p.status, saldo_do_insumo(self.farinha)), ("novo", D("10")))


class FilaETransicoesTests(PedidoBase):
    def test_fluxo_completo_e_historico(self):
        p = self.pedido("lancado")
        item = p.itens.get().venda_item
        s.avancar(self.req_o, p)
        item.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual((p.status, item.preparado), ("em_preparo", True))
        s.avancar(self.req_o, p)
        s.avancar(self.req_o, p)
        p.refresh_from_db()
        self.assertEqual((p.status, bool(p.entregue_em)), ("entregue", True))
        self.assertEqual([e.para_status for e in p.eventos.all()], ["novo", "confirmado", "em_preparo", "pronto", "entregue"])
        with self.assertRaises(RegraNegocioError):
            s.avancar(self.req_o, p)
        with self.assertRaises(RegraNegocioError):
            s.voltar(self.req_o, p)

    def test_voltar_corrige_um_passo_e_desfaz_preparado(self):
        p = self.pedido("lancado")
        s.avancar(self.req_o, p)
        s.voltar(self.req_o, p)
        p.refresh_from_db()
        self.assertEqual(p.status, "confirmado")
        self.assertFalse(p.itens.get().venda_item.preparado)
        s.avancar(self.req_o, p)
        s.avancar(self.req_o, p)
        s.voltar(self.req_o, p)
        p.refresh_from_db()
        self.assertEqual(p.status, "em_preparo")
        with self.assertRaises(RegraNegocioError):
            s.voltar(self.req_o, self.pedido("manual"))  # novo não volta

    def test_fila_so_traz_ativos_na_ordem(self):
        a = self.pedido("lancado")
        venda_b = pdv.abrir_comanda(self.req_o, self.caixa, Mesa.objects.create(cliente=self.cliente, nome="Mesa 6"))
        pdv.adicionar_item(self.req_o, venda_b, self.bolo.pk, 1)
        b, _ = s.criar_pedido(self.req_o, venda_b)
        s.avancar(self.req_o, a)
        ordem = [p.pk for p in s.fila([self.cliente])]
        self.assertEqual(ordem, [a.pk, b.pk])
        s.cancelar(self.req_g, b, "desistiu")
        self.assertEqual([p.pk for p in s.fila([self.cliente])], [a.pk])

    def test_evento_e_imutavel_e_auditoria(self):
        p = self.pedido("lancado")
        evento = p.eventos.first()
        evento.nota = "x"
        with self.assertRaises(HistoricoImutavelError):
            evento.save()
        with self.assertRaises(HistoricoImutavelError):
            PedidoEvento.objects.all().delete()
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="pedido", acao="criar").exists())
        s.avancar(self.req_o, p)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="pedido", acao="editar", descricao__contains="Em preparo").exists())


class CancelamentoTests(PedidoBase):
    def test_cancelar_antes_do_preparo_repoe_estoque_e_ajusta_comanda(self):
        p = self.pedido("lancado")
        venda = p.venda
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))
        s.cancelar(self.req_g, p, "cliente saiu")
        p.refresh_from_db()
        venda.refresh_from_db()
        self.assertEqual((p.status, p.motivo_cancelamento), ("cancelado", "cliente saiu"))
        self.assertEqual((saldo_do_insumo(self.farinha), venda.subtotal, venda.itens.count()), (D("10"), 0, 0))
        self.assertEqual(p.itens.get().venda_item_id, None)  # snapshot do item continua no pedido

    def test_cancelar_em_preparo_nao_repoe(self):
        p = self.pedido("lancado")
        s.avancar(self.req_o, p)
        s.cancelar(self.req_g, p, "queimou")
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))  # insumo já consumido

    def test_nao_cancela_venda_paga_nem_pedido_encerrado_nem_sem_motivo(self):
        p = self.pedido("lancado")
        with self.assertRaises(RegraNegocioError):
            s.cancelar(self.req_g, p, " ")
        pdv.pagar_comanda(self.req_o, p.venda, [{"forma": self.pix.pk, "valor": 1000}])
        with self.assertRaises(RegraNegocioError):
            s.cancelar(self.req_g, p, "tarde")
        for _ in range(3):
            s.avancar(self.req_o, p)
        with self.assertRaises(RegraNegocioError):
            s.cancelar(self.req_g, p, "entregue")

    def test_cancelar_a_venda_encerra_os_pedidos_ativos(self):
        p = self.pedido("lancado")
        pdv.cancelar_venda(self.req_g, p.venda, "mesa desistiu")
        p.refresh_from_db()
        self.assertEqual(p.status, "cancelado")
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))

    def test_item_enviado_a_cozinha_nao_sai_da_comanda_pelo_pdv(self):
        p = self.pedido("lancado")
        with self.assertRaises(RegraNegocioError):
            pdv.remover_item(self.req_o, p.venda, p.itens.get().venda_item_id)


class IntegracaoPdvTests(PedidoBase):
    def test_baixa_por_item_nao_duplica_entre_confirmacao_e_recebimento(self):
        self.regra("lancado")
        venda = self.comanda(qtd=2)
        s.criar_pedido(self.req_o, venda)  # confirma: baixa 2 bolos
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 0)  if False else None
        venda.refresh_from_db()
        outro = pdv.adicionar_item  # segunda rodada de itens (outro produto igual cai na mesma linha: usa novo item)
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))
        pdv.pagar_comanda(self.req_o, venda, [{"forma": self.pix.pk, "valor": 1000}])
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))  # recebimento não baixa de novo
        self.assertEqual(MovimentoEstoque.objects.filter(observacao__startswith="Venda PDV").count(), 1)

    def test_duas_rodadas_enviam_so_os_itens_pendentes(self):
        self.regra("lancado")
        venda = pdv.abrir_comanda(self.req_o, self.caixa, self.mesa)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        p1, _ = s.criar_pedido(self.req_o, venda)
        sem_novos, criado = s.criar_pedido(self.req_o, venda)
        self.assertEqual((sem_novos, criado), (None, False))
        # segunda rodada: item novo (outro produto)
        from apps.ficha.tests import criar as criar_ficha, produto as criar_produto
        from apps.precificacao.tests import nova_precificacao

        torta = criar_produto(self.cliente, "Torta")
        nova_precificacao(self.cliente, criar_ficha(self.cliente, "Torta", [(self.farinha, "g", "100")], prod=torta), usar_no_pdv=True)
        pdv.adicionar_item(self.req_o, venda, torta.pk, 2)
        p2, criado = s.criar_pedido(self.req_o, venda)
        self.assertTrue(criado)
        self.assertEqual((p2.itens.count(), p1.itens.count(), p2.numero), (1, 1, 2))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.55"))  # 250 g + 2 × 100 g

    def test_balcao_com_cozinha_cria_pedido_apos_a_venda(self):
        self.regra("lancado")
        venda, _ = self.vender(self.caixa, qtd=1, cozinha="balcao")
        pedido = Pedido.objects.get(venda=venda)
        self.assertEqual((pedido.tipo, pedido.status, saldo_do_insumo(self.farinha)), ("balcao", "confirmado", D("9.75")))

    def test_idempotencia_por_chave(self):
        self.regra("lancado")
        venda = self.comanda()
        a, c1 = s.criar_pedido(self.req_o, venda, chave="p-1")
        b, c2 = s.criar_pedido(self.req_o, venda, chave="p-1")
        self.assertEqual((c1, c2, a.pk), (True, False, b.pk))
        self.assertEqual(Pedido.objects.count(), 1)


class TelasTests(PedidoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.operador)

    def test_qualquer_perfil_move_mas_so_gestor_cancela(self):
        p = self.pedido("lancado")
        url = reverse("cozinha-api-mover", args=[p.pk])
        r = self.client.post(url, json.dumps({"acao": "avancar"}), content_type="application/json")
        self.assertEqual((r.status_code, r.json()["pedido"]["status"]), (200, "em_preparo"))
        r = self.client.post(url, json.dumps({"acao": "voltar"}), content_type="application/json")
        self.assertEqual(r.json()["pedido"]["status"], "confirmado")
        self.assertEqual(self.client.post(url, json.dumps({"acao": "xyz"}), content_type="application/json").status_code, 422)
        self.assertEqual(self.client.post(url, "nao-json", content_type="application/json").status_code, 422)
        self.assertEqual(self.client.post(reverse("pedido-acao", args=[p.pk]), {"acao": "cancelar", "motivo": "x"}).status_code, 404)  # operador não cancela
        p.refresh_from_db()
        self.assertEqual(p.status, "confirmado")
        self.client.force_login(self.gestor)
        self.client.post(reverse("pedido-acao", args=[p.pk]), {"acao": "cancelar", "motivo": "teste"})
        p.refresh_from_db()
        self.assertEqual(p.status, "cancelado")

    def test_transicao_invalida_nao_muda_nada(self):
        p = self.pedido("lancado")
        for _ in range(3):
            s.avancar(self.req_o, p)
        r = self.client.post(reverse("cozinha-api-mover", args=[p.pk]), json.dumps({"acao": "avancar"}), content_type="application/json")
        self.assertEqual(r.status_code, 422)
        p.refresh_from_db()
        self.assertEqual(p.status, "entregue")

    def test_fila_json_e_paginas(self):
        p = self.pedido("lancado")
        fila = self.client.get(reverse("cozinha-api-fila")).json()["pedidos"]
        self.assertEqual([(f["id"], f["status"], f["mesa"]) for f in fila], [(p.pk, "confirmado", "Mesa 5")])
        self.assertEqual(fila[0]["itens"][0]["descricao"], "Bolo")
        for nome, args in (("cozinha", []), ("pedidos", []), ("pedido-detalhe", [p.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200, nome)
        pagina = self.client.get(reverse("cozinha")).content.decode()
        self.assertIn("Atalhos", pagina)
        self.assertIn('aria-live="polite"', pagina)

    def test_outro_tenant_nao_ve_nem_move(self):
        p = self.pedido("lancado")
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("pedido-detalhe", args=[p.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("cozinha-api-mover", args=[p.pk]), json.dumps({"acao": "avancar"}), content_type="application/json").status_code, 404)
        self.assertEqual(self.client.post(reverse("pedido-acao", args=[p.pk]), {"acao": "avancar"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("cozinha-api-fila")).json()["pedidos"], [])
        self.assertEqual(self.client.post(reverse("pedido-enviar", args=[p.venda_id])).status_code, 404)

    def test_enviar_da_comanda_pela_tela_e_operador_so_na_propria_venda(self):
        self.regra("lancado")
        venda = self.comanda()
        r = self.client.post(reverse("pedido-enviar", args=[venda.pk]), {"tipo": "mesa"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Pedido.objects.filter(venda=venda).count(), 1)
        self.client.post(reverse("pedido-enviar", args=[venda.pk]), {"tipo": "mesa"})  # nada pendente
        self.assertEqual(Pedido.objects.filter(venda=venda).count(), 1)
        outra = pdv.abrir_caixa(self.req_g, 0)
        venda_g = pdv.abrir_comanda(self.req_g, outra, Mesa.objects.create(cliente=self.cliente, nome="Mesa G"))
        pdv.adicionar_item(self.req_g, venda_g, self.bolo.pk, 1)
        self.assertEqual(self.client.post(reverse("pedido-enviar", args=[venda_g.pk])).status_code, 404)

    def test_sem_permissao_de_mover_403(self):
        p = self.pedido("lancado")
        self.operador.groups.first().permissions.remove(Permission.objects.get(codename="mover_pedido"))
        self.client.force_login(self.operador)
        self.assertEqual(self.client.post(reverse("cozinha-api-mover", args=[p.pk]), json.dumps({"acao": "avancar"}), content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(reverse("cozinha")).status_code, 200)  # ainda vê a fila


class APITests(PedidoBase):
    def setUp(self):
        super().setUp()
        self.api_o, self.api_g, self.api_b = self.api(self.operador), self.api(self.gestor), self.api(self.gestor_b)

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def ok(self, r, codigo=200):
        self.assertEqual(r.status_code, codigo, r.content[:400])
        return r.json().get("data")

    def corpo(self, **extra):
        return {"tipo": "entrega", "itens": [{"produto": self.bolo.pk, "quantidade": 2, "observacao": "sem açúcar"}], **extra}

    def test_criar_idempotente_e_fluxo(self):
        self.regra("manual")
        d1 = self.ok(self.api_o.post(f"{V1}/pedidos/", self.corpo(), format="json", HTTP_IDEMPOTENCY_KEY="app-9"), 201)
        d2 = self.ok(self.api_o.post(f"{V1}/pedidos/", self.corpo(), format="json", HTTP_IDEMPOTENCY_KEY="app-9"), 200)
        self.assertEqual((d1["id"], d2["id"], d2["criado"], d1["status"], d1["tipo"]), (d1["id"], d1["id"], False, "novo", "entrega"))
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(Venda.objects.filter(origem="pedido", status="aberta").count(), 1)
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))
        pid = d1["id"]
        d = self.ok(self.api_o.post(f"{V1}/pedidos/{pid}/confirmar/"))
        self.assertEqual((d["status"], saldo_do_insumo(self.farinha)), ("confirmado", D("9.5")))
        for esperado in ("em_preparo", "pronto", "entregue"):
            self.assertEqual(self.ok(self.api_o.post(f"{V1}/pedidos/{pid}/avancar/"))["status"], esperado)
        self.assertEqual(self.api_o.post(f"{V1}/pedidos/{pid}/avancar/").status_code, 422)
        detalhe = self.ok(self.api_o.get(f"{V1}/pedidos/{pid}/"))
        self.assertEqual([e["para"] for e in detalhe["eventos"]], ["novo", "confirmado", "em_preparo", "pronto", "entregue"])
        self.assertEqual(detalhe["itens"][0]["observacao"], "sem açúcar")

    def test_regra_lancado_na_criacao_e_fila(self):
        self.regra("lancado")
        d = self.ok(self.api_o.post(f"{V1}/pedidos/", self.corpo(tipo="retirada"), format="json"), 201)
        self.assertEqual(d["status"], "confirmado")
        fila = self.ok(self.api_o.get(f"{V1}/pedidos/fila/"))
        self.assertEqual([f["id"] for f in fila], [d["id"]])
        self.assertEqual(len(self.ok(self.api_o.get(f"{V1}/pedidos/", {"status": "confirmado"}))["itens"]), 1)

    def test_validacoes_permissoes_e_tenant(self):
        self.assertEqual(self.api_o.post(f"{V1}/pedidos/", {"itens": []}, format="json").status_code, 422)
        self.assertEqual(self.api_o.post(f"{V1}/pedidos/", self.corpo(tipo="xyz"), format="json").status_code, 422)
        self.assertEqual(self.api_o.post(f"{V1}/pedidos/", self.corpo(mesa=999999), format="json").status_code, 400)
        self.regra("lancado")
        d = self.ok(self.api_o.post(f"{V1}/pedidos/", self.corpo(), format="json"), 201)
        pid = d["id"]
        self.assertEqual(self.api_o.post(f"{V1}/pedidos/{pid}/cancelar/", {"motivo": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api_b.get(f"{V1}/pedidos/{pid}/").status_code, 404)
        self.assertEqual(self.api_b.post(f"{V1}/pedidos/{pid}/avancar/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/pedidos/fila/").json()["data"], [])
        self.assertEqual(APIClient().get(f"{V1}/pedidos/").status_code, 401)
        c = self.ok(self.api_g.post(f"{V1}/pedidos/{pid}/cancelar/", {"motivo": "cliente"}, format="json"))
        self.assertEqual((c["status"], saldo_do_insumo(self.farinha)), ("cancelado", D("10")))

    def test_pagamento_do_pedido_pela_api_do_pdv(self):
        self.regra("pagamento")
        d = self.ok(self.api_o.post(f"{V1}/pedidos/", self.corpo(), format="json"), 201)
        self.assertEqual(d["status"], "novo")
        r = self.api_o.post(f"{V1}/pdv/comandas/{d['venda']}/pagar/", {"pagamentos": [{"forma": self.pix.pk, "valor": 1000}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(self.ok(self.api_o.get(f"{V1}/pedidos/{d['id']}/"))["status"], "confirmado")
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))
