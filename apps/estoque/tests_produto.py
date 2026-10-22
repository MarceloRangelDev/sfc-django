"""Estoque de PRODUTOS (saldo próprio): entrada, baixa manual com motivo, baixa automática pelo PDV (idempotente), estorno por
cancelamento/devolução, consistência com o histórico, permissões, isolamento entre empresas (tela e API) e o motivo
obrigatório da baixa manual de insumos."""
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.estoque.models import MovimentoEstoque, MovimentoProduto, SaldoProduto
from apps.estoque.produto_estoque import (
    baixar_produto_da_venda, estornar_produto_da_venda, registrar_baixa_manual_produto, registrar_entrada_produto,
    saldo_do_produto, verificar_consistencia_produtos,
)
from apps.estoque.services import registrar_entrada, saldo_do_insumo
from apps.ficha.tests import criar as criar_ficha
from apps.ficha.tests import produto as criar_produto
from apps.insumo.tests import medida, novo_insumo
from apps.pdv import services as pdv
from apps.pdv.models import Venda
from apps.pdv.tests import PdvBase, Req, novo_usuario
from apps.pdv.tests_api import V1, APIPdvBase
from apps.precificacao.tests import nova_precificacao
from web_project.regras import RegraNegocioError

D = Decimal


class ProdutoEstoqueBase(PdvBase):
    """Empresa A (gestor/operador) e B (gestor_b); o Bolo da A controla estoque próprio."""

    def setUp(self):
        super().setUp()
        self.bolo.controla_estoque = True
        self.bolo.save()
        self.visualizador = novo_usuario("visual", self.cliente, "Visualizador")
        self.bolo_b = criar_produto(self.outro, "Bolo B")
        self.bolo_b.controla_estoque = True
        self.bolo_b.save()


class ServicoManualTests(ProdutoEstoqueBase):
    def test_entrada_baixa_manual_e_historico(self):
        registrar_entrada_produto(self.gestor, self.bolo, D("10"))
        movimento = registrar_baixa_manual_produto(self.gestor, self.bolo, D("3"), "perda", "caiu no chão")
        self.assertEqual((saldo_do_produto(self.bolo), movimento.saldo_apos, movimento.motivo), (D("7"), D("7"), "perda"))
        self.assertEqual(MovimentoProduto.objects.filter(produto=self.bolo).count(), 2)
        self.assertEqual(verificar_consistencia_produtos(), [])

    def test_baixa_manual_exige_motivo_da_lista_e_observacao_em_outro(self):
        registrar_entrada_produto(self.gestor, self.bolo, D("5"))
        for motivo, obs in (("", ""), ("inventado", ""), ("outro", "  ")):
            with self.assertRaises(RegraNegocioError):
                registrar_baixa_manual_produto(self.gestor, self.bolo, D("1"), motivo, obs)
        registrar_baixa_manual_produto(self.gestor, self.bolo, D("1"), "outro", "doação")
        self.assertEqual(saldo_do_produto(self.bolo), D("4"))

    def test_saldo_nunca_fica_negativo(self):
        registrar_entrada_produto(self.gestor, self.bolo, D("2"))
        with self.assertRaises(RegraNegocioError):
            registrar_baixa_manual_produto(self.gestor, self.bolo, D("2.001"), "perda")
        self.assertEqual(saldo_do_produto(self.bolo), D("2"))
        with self.assertRaises(IntegrityError), transaction.atomic():  # rede de segurança do banco
            SaldoProduto.objects.filter(produto=self.bolo).update(saldo=D("-1"))

    def test_produto_sem_controle_nao_movimenta(self):
        self.bolo.controla_estoque = False
        self.bolo.save()
        with self.assertRaises(RegraNegocioError):
            registrar_entrada_produto(self.gestor, self.bolo, D("1"))

    def test_historico_e_imutavel(self):
        movimento = registrar_entrada_produto(self.gestor, self.bolo, D("1"))
        from web_project.historico import HistoricoImutavelError

        with self.assertRaises(HistoricoImutavelError):
            movimento.quantidade = D("9")
            movimento.save()
        with self.assertRaises(HistoricoImutavelError):
            movimento.delete()

    def test_quantidade_invalida(self):
        for quantidade in (D("0"), D("-1"), D("0.0004")):
            with self.assertRaises(RegraNegocioError):
                registrar_entrada_produto(self.gestor, self.bolo, quantidade)


class VendaPdvTests(ProdutoEstoqueBase):
    def setUp(self):
        super().setUp()
        registrar_entrada_produto(self.gestor, self.bolo, D("5"))
        self.caixa = self.abrir()

    def test_venda_baixa_produto_e_insumos_e_grava_rastreio(self):
        venda, _ = self.vender(self.caixa, qtd=2)
        self.assertEqual(saldo_do_produto(self.bolo), D("3"))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))  # a baixa dos insumos da ficha continua
        movimento = MovimentoProduto.objects.get(tipo="baixa_venda")
        self.assertEqual((movimento.venda_item.venda_id, movimento.quantidade, movimento.saldo_apos), (venda.pk, D("2"), D("3")))

    def test_baixa_nao_se_repete_em_reprocessamento_ou_repeticao(self):
        venda, _ = self.vender(self.caixa, qtd=2, chave="k1")
        item = venda.itens.get()
        self.assertIsNone(baixar_produto_da_venda(self.gestor, item))  # reprocessar: nada novo
        pdv.baixar_estoque(self.gestor, venda)  # itens já marcados como baixados
        self.vender(self.caixa, qtd=2, chave="k1")  # mesma chave de idempotência
        self.assertEqual((saldo_do_produto(self.bolo), MovimentoProduto.objects.filter(tipo="baixa_venda").count()), (D("3"), 1))
        # mesmo que alguém tente gravar a 2ª baixa direto, o banco recusa
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimentoProduto.objects.create(
                cliente=self.cliente, produto=self.bolo, tipo="baixa_venda", quantidade=D("1"), saldo_apos=D("2"), venda_item=item,
            )

    def test_saldo_insuficiente_recusa_a_venda_inteira(self):
        antes = Venda.objects.count()
        with self.assertRaises(RegraNegocioError) as erro:
            self.vender(self.caixa, qtd=6)
        self.assertIn("Bolo", str(erro.exception))
        self.assertEqual((Venda.objects.count(), saldo_do_produto(self.bolo), saldo_do_insumo(self.farinha)), (antes, D("5"), D("10")))
        self.assertFalse(MovimentoProduto.objects.filter(tipo="baixa_venda").exists())

    def test_produto_sem_controle_vende_sem_movimento(self):
        self.bolo.controla_estoque = False
        self.bolo.save()
        self.vender(self.caixa, qtd=1)
        self.assertEqual(saldo_do_produto(self.bolo), D("5"))
        self.assertFalse(MovimentoProduto.objects.filter(tipo="baixa_venda").exists())

    def test_cancelar_estorna_o_saldo_uma_vez(self):
        venda, _ = self.vender(self.caixa, qtd=4, pagamentos=[{"forma": self.dinheiro.pk, "valor": 2000}])
        pdv.cancelar_venda(self.req_g, venda, "desistiu")
        self.assertEqual(saldo_do_produto(self.bolo), D("5"))
        item = venda.itens.get()
        self.assertFalse(estornar_produto_da_venda(self.gestor, item, D("1")))  # nada mais a devolver
        self.assertEqual(saldo_do_produto(self.bolo), D("5"))
        self.assertEqual(verificar_consistencia_produtos(), [])

    def test_devolucao_parcial_estorna_so_o_devolvido(self):
        venda, _ = self.vender(self.caixa, qtd=4, pagamentos=[{"forma": self.dinheiro.pk, "valor": 2000}])
        self.assertEqual(saldo_do_produto(self.bolo), D("1"))
        self.abrir(self.gestor, 10000)
        pdv.devolver_item(self.req_g, venda.itens.get(), D("1"), "queimado", self.dinheiro, repor_estoque=True)
        self.assertEqual(saldo_do_produto(self.bolo), D("2"))
        pdv.devolver_item(self.req_g, venda.itens.get(), D("1"), "outro", self.dinheiro, repor_estoque=False)
        self.assertEqual(saldo_do_produto(self.bolo), D("2"))  # sem pedir reposição, nada volta

    def test_item_preparado_nao_repoe(self):
        venda, _ = self.vender(self.caixa, qtd=1)
        venda.itens.update(preparado=True)
        pdv.cancelar_venda(self.req_g, venda, "erro")
        self.assertEqual(saldo_do_produto(self.bolo), D("4"))

    def test_comando_verificar_estoque_acha_e_corrige_divergencia(self):
        SaldoProduto.objects.filter(produto=self.bolo).update(saldo=D("99"))
        self.assertEqual(len(verificar_consistencia_produtos()), 1)
        verificar_consistencia_produtos(corrigir=True)
        self.assertEqual(saldo_do_produto(self.bolo), D("5"))


class TelasTests(ProdutoEstoqueBase):
    def setUp(self):
        super().setUp()
        registrar_entrada_produto(self.gestor, self.bolo, D("5"))
        registrar_entrada_produto(self.gestor_b, self.bolo_b, D("7")) if False else None

    def test_listas_mostram_so_a_propria_empresa(self):
        MovimentoProduto.objects.create(cliente=self.outro, produto=self.bolo_b, tipo="entrada", quantidade=D("7"), saldo_apos=D("7"))
        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("estoque-produtos"))
        self.assertContains(pagina, "Bolo")
        self.assertNotContains(pagina, "Bolo B")
        historico = self.client.get(reverse("estoque-produto-movimentos"))
        self.assertContains(historico, "Entrada")
        self.assertEqual({m.cliente_id for m in historico.context["movimentos"]}, {self.cliente.pk})

    def test_movimentar_pela_tela_com_motivo(self):
        self.client.force_login(self.operador)
        url = reverse("estoque-produto-movimentar")
        self.assertEqual(self.client.get(url).status_code, 200)
        sem_motivo = self.client.post(url, {"produto": self.bolo.pk, "tipo": "saida", "quantidade": "1"})
        self.assertEqual(sem_motivo.status_code, 200)  # volta ao formulário com erro
        self.assertIn("motivo", sem_motivo.context["form"].errors)
        self.assertEqual(saldo_do_produto(self.bolo), D("5"))
        ok = self.client.post(url, {"produto": self.bolo.pk, "tipo": "saida", "quantidade": "1,5", "motivo": "quebra"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(saldo_do_produto(self.bolo), D("3.5"))
        entrada = self.client.post(url, {"produto": self.bolo.pk, "tipo": "entrada", "quantidade": "2", "motivo": "perda"})
        self.assertEqual(entrada.status_code, 302)  # motivo é ignorado na entrada
        self.assertEqual(MovimentoProduto.objects.filter(tipo="entrada").latest("pk").motivo, "")

    def test_produto_de_outra_empresa_e_recusado(self):
        self.client.force_login(self.gestor)
        resposta = self.client.post(reverse("estoque-produto-movimentar"), {"produto": self.bolo_b.pk, "tipo": "entrada", "quantidade": "1"})
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("produto", resposta.context["form"].errors)
        self.assertFalse(MovimentoProduto.objects.filter(produto=self.bolo_b).exists())

    def test_visualizador_ve_mas_nao_movimenta(self):
        self.client.force_login(self.visualizador)
        self.assertEqual(self.client.get(reverse("estoque-produtos")).status_code, 200)
        self.assertEqual(self.client.get(reverse("estoque-produto-movimentos")).status_code, 200)
        self.assertEqual(self.client.get(reverse("estoque-produto-movimentar")).status_code, 403)
        self.assertEqual(self.client.post(reverse("estoque-produto-movimentar"), {"produto": self.bolo.pk, "tipo": "entrada", "quantidade": "1"}).status_code, 403)

    def test_anonimo_vai_para_o_login(self):
        self.assertEqual(self.client.get(reverse("estoque-produtos")).status_code, 302)

    def test_cadastro_do_produto_liga_o_controle_e_servico_nao_tem(self):
        from apps.produto.models import Produto

        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("produto-edit", args=[self.bolo.pk]))
        self.assertContains(pagina, "controla_estoque")
        Produto.objects.filter(pk=self.bolo.pk).update(controla_estoque=False)
        resposta = self.client.post(reverse("produto-edit", args=[self.bolo.pk]), {
            "nome": "Bolo", "categoria": self.bolo.categoria_id, "controla_estoque": "on", "ativo": "on", "descricao": "", "codigo_barras": "",
        })
        self.assertEqual(resposta.status_code, 302, getattr(getattr(resposta, "context", None), "get", lambda k: None)("form"))
        self.bolo.refresh_from_db()
        self.assertTrue(self.bolo.controla_estoque)


class MotivoDaBaixaDeInsumoTests(PdvBase):
    """Baixa manual de INSUMO (tela e API) passa a exigir motivo; a baixa automática do PDV não."""

    def test_tela_exige_motivo_na_saida_e_nao_na_entrada(self):
        self.client.force_login(self.gestor)
        url = reverse("estoque-movimentar")
        recusada = self.client.post(url, {"insumo": self.farinha.pk, "tipo": "saida", "quantidade": "1", "medida": medida("kg").pk})
        self.assertEqual(recusada.status_code, 200)
        self.assertIn("motivo", recusada.context["form"].errors)
        ok = self.client.post(url, {"insumo": self.farinha.pk, "tipo": "saida", "quantidade": "1", "medida": medida("kg").pk, "motivo": "vencimento"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(MovimentoEstoque.objects.filter(tipo="saida").latest("pk").motivo, "vencimento")
        entrada = self.client.post(url, {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "1", "medida": medida("kg").pk})
        self.assertEqual(entrada.status_code, 302)

    def test_outro_exige_observacao(self):
        self.client.force_login(self.gestor)
        resposta = self.client.post(reverse("estoque-movimentar"), {
            "insumo": self.farinha.pk, "tipo": "ajuste_saida", "quantidade": "1", "medida": medida("kg").pk, "motivo": "outro",
        })
        self.assertIn("observacao", resposta.context["form"].errors)

    def test_baixa_do_pdv_nao_precisa_de_motivo(self):
        caixa = self.abrir()
        self.vender(caixa, qtd=1)
        self.assertEqual(MovimentoEstoque.objects.filter(observacao__startswith="Venda PDV").latest("pk").motivo, "")


class ApiProdutoEstoqueTests(APIPdvBase):
    def setUp(self):
        super().setUp()
        self.bolo.controla_estoque = True
        self.bolo.save()
        self.bolo_b = criar_produto(self.outro, "Bolo B")
        self.bolo_b.controla_estoque = True
        self.bolo_b.save()
        self.api_v = self.api(novo_usuario("visual", self.cliente, "Visualizador"))

    def movimentar(self, api, **corpo):
        return api.post(f"{V1}/estoque/produtos/movimentos/", {"produto": self.bolo.pk, "tipo": "entrada", "quantidade": "5", **corpo}, format="json")

    def test_entrada_baixa_com_motivo_e_listagens(self):
        self.ok(self.movimentar(self.api_g), 201)
        recusada = self.movimentar(self.api_g, tipo="saida", quantidade="1")
        self.assertEqual(recusada.status_code, 400)
        self.assertIn("motivo", recusada.json()["errors"])
        baixa = self.ok(self.movimentar(self.api_g, tipo="saida", quantidade="2", motivo="perda", observacao="x"), 201)
        self.assertEqual((baixa["saldo_apos"], baixa["motivo"], baixa["venda"]), ("3.000", "perda", None))
        saldos = self.ok(self.api_g.get(f"{V1}/estoque/produtos/saldos/"))
        self.assertEqual([(i["nome"], i["saldo"]) for i in saldos["itens"]], [("Bolo", "3.000")])
        movimentos = self.ok(self.api_g.get(f"{V1}/estoque/produtos/movimentos/?tipo=saida"))
        self.assertEqual(len(movimentos["itens"]), 1)

    def test_saldo_insuficiente_e_422(self):
        self.ok(self.movimentar(self.api_g, quantidade="1"), 201)
        self.assertEqual(self.movimentar(self.api_g, tipo="saida", quantidade="2", motivo="perda").status_code, 422)

    def test_isolamento_entre_empresas(self):
        self.ok(self.movimentar(self.api_g), 201)
        # produto de outra empresa: não existe para quem chama
        r = self.movimentar(self.api_g, produto=self.bolo_b.pk)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MovimentoProduto.objects.filter(produto=self.bolo_b).exists())
        # a outra empresa não vê saldos nem movimentos da primeira
        self.assertEqual(self.ok(self.api_b.get(f"{V1}/estoque/produtos/saldos/"))["itens"][0]["nome"], "Bolo B")
        self.assertEqual(self.ok(self.api_b.get(f"{V1}/estoque/produtos/movimentos/"))["itens"], [])
        movimento = MovimentoProduto.objects.get(produto=self.bolo)
        self.assertEqual(self.api_b.get(f"{V1}/estoque/produtos/movimentos/{movimento.pk}/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/estoque/produtos/saldos/{self.bolo.pk}/").status_code, 404)

    def test_permissoes_e_autenticacao(self):
        self.assertEqual(self.api_v.get(f"{V1}/estoque/produtos/saldos/").status_code, 200)
        self.assertEqual(self.movimentar(self.api_v).status_code, 403)
        self.assertEqual(APIClient().get(f"{V1}/estoque/produtos/saldos/").status_code, 401)
        self.assertEqual(self.api_g.put(f"{V1}/estoque/produtos/movimentos/1/", {}, format="json").status_code, 405)  # histórico imutável

    def test_cadastro_de_produto_expoe_controla_estoque(self):
        dados = self.ok(self.api_g.get(f"{V1}/produtos/{self.bolo.pk}/"))
        self.assertTrue(dados["controla_estoque"])
        self.ok(self.api_g.patch(f"{V1}/produtos/{self.bolo.pk}/", {"controla_estoque": False}, format="json"))
        self.bolo.refresh_from_db()
        self.assertFalse(self.bolo.controla_estoque)

    def test_motivo_obrigatorio_tambem_na_api_de_insumos(self):
        corpo = {"insumo": self.farinha.pk, "tipo": "saida", "quantidade": "1"}
        r = self.api_g.post(f"{V1}/estoque/movimentos/", corpo, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("motivo", r.json()["errors"])
        ok = self.ok(self.api_g.post(f"{V1}/estoque/movimentos/", {**corpo, "motivo": "consumo_interno"}, format="json"), 201)
        self.assertEqual(ok[0]["motivo"], "consumo_interno")
