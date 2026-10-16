"""PDV: caixa, vendas (balcão e comanda), pagamentos com taxa, estoque, financeiro, cancelamento, devolução, comprovante,
permissões (Operador só vende), tenant e idempotência."""
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.consumidor.models import Consumidor
from apps.estoque.models import MovimentoEstoque
from apps.estoque.services import registrar_entrada, saldo_do_insumo
from apps.ficha.tests import criar as criar_ficha
from apps.ficha.tests import produto as criar_produto
from apps.financeiro.models import ContaPagar, ContaReceber
from apps.insumo.tests import medida, novo_insumo
from apps.pdvconfig.models import ConfiguracaoPdv, FormaPagamento, Mesa
from apps.precificacao.models import Precificacao
from apps.precificacao.tests import nova_precificacao
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import services as s
from .models import Caixa, Devolucao, MovimentoCaixa, Venda, VendaItem, VendaItemInsumo, VendaPagamento

D = Decimal


class Req:
    """Request mínimo para os serviços (usuário + META para o IP da auditoria)."""

    def __init__(self, user):
        self.user, self.META = user, {}


def novo_usuario(nome, cliente, grupo):
    usuario = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(Group.objects.get(name=grupo))
    return usuario


class PdvBase(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Padaria A")
        self.outro = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11444777000161", nome_razao_social="Padaria B")
        for c in (self.cliente, self.outro):
            for modulo in ("insumo", "produto", "ficha", "precificacao", "estoque", "pdv"):
                habilitar_modulo(c, modulo)
        self.gestor = novo_usuario("gestor", self.cliente, "Gestor")
        self.operador = novo_usuario("operador", self.cliente, "Operador")
        self.gestor_b = novo_usuario("gestor_b", self.outro, "Gestor")
        # farinha R$ 10,00/kg; bolo usa 250 g (custo 250); preço do PDV = markup 100% -> 500
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        self.bolo = criar_produto(self.cliente, "Bolo")
        self.ficha = criar_ficha(self.cliente, "Bolo simples", [(self.farinha, "g", "250")], prod=self.bolo)
        self.preco = nova_precificacao(self.cliente, self.ficha, percentual_lucro=D("100"), usar_no_pdv=True)
        registrar_entrada(self.gestor, self.farinha, D("10"), medida("kg"))
        self.dinheiro = FormaPagamento.objects.create(cliente=self.cliente, nome="Dinheiro", tipo="dinheiro")
        self.pix = FormaPagamento.objects.create(cliente=self.cliente, nome="PIX", tipo="pix")
        self.credito = FormaPagamento.objects.create(cliente=self.cliente, nome="Crédito", tipo="credito", taxa_modo="ambas", taxa_percentual=D("3"), taxa_fixa=50)
        self.req_g, self.req_o = Req(self.gestor), Req(self.operador)

    def abrir(self, usuario=None, abertura=10000):
        return s.abrir_caixa(Req(usuario or self.operador), abertura)

    def vender(self, caixa, qtd=2, pagamentos=None, **extra):
        total = 500 * qtd
        pagamentos = pagamentos or [{"forma": self.dinheiro.pk, "valor": total}]
        return s.registrar_venda(Req(caixa.operador), caixa, [{"produto": self.bolo.pk, "quantidade": qtd}], pagamentos, **extra)


class FormaPagamentoTests(TestCase):
    def setUp(self):
        self.c = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="X")

    def forma(self, **campos):
        f = FormaPagamento(cliente=self.c, nome="Teste", **campos)
        f.full_clean()
        return f

    def test_modos_de_taxa(self):
        self.assertEqual(self.forma(taxa_modo="sem_taxa").calcular_taxa(10000), 0)
        self.assertEqual(self.forma(taxa_modo="percentual", taxa_percentual=D("3.5")).calcular_taxa(10000), 350)
        self.assertEqual(self.forma(taxa_modo="fixa", taxa_fixa=50).calcular_taxa(10000), 50)
        self.assertEqual(self.forma(taxa_modo="ambas", taxa_percentual=D("3"), taxa_fixa=50).calcular_taxa(10000), 350)

    def test_campos_que_o_modo_nao_usa_sao_zerados(self):
        f = self.forma(taxa_modo="percentual", taxa_percentual=D("2"), taxa_fixa=999)
        self.assertEqual(f.taxa_fixa, 0)
        f = self.forma(taxa_modo="fixa", taxa_percentual=D("2"), taxa_fixa=100)
        self.assertEqual(f.taxa_percentual, 0)
        self.assertEqual(self.forma(taxa_modo="sem_taxa", taxa_percentual=D("5"), taxa_fixa=5).calcular_taxa(1000), 0)

    def test_modo_exige_os_valores(self):
        from django.core.exceptions import ValidationError

        for campos in ({"taxa_modo": "percentual"}, {"taxa_modo": "fixa"}, {"taxa_modo": "ambas", "taxa_percentual": D("2")}):
            with self.assertRaises(ValidationError, msg=campos):
                self.forma(**campos)

    def test_arredondamento_meio_para_cima_e_teto(self):
        f = self.forma(taxa_modo="percentual", taxa_percentual=D("2.5"))
        self.assertEqual(f.calcular_taxa(10), 0)  # 0,25 -> 0
        self.assertEqual(f.calcular_taxa(20), 1)  # 0,5 -> 1
        self.assertEqual(self.forma(taxa_modo="fixa", taxa_fixa=500).calcular_taxa(100), 100)  # nunca passa do valor


class CaixaTests(PdvBase):
    def test_abre_um_por_operador_e_audita(self):
        caixa = self.abrir()
        self.assertEqual((caixa.status, caixa.valor_abertura, caixa.cliente_id), ("aberto", 10000, self.cliente.pk))
        with self.assertRaises(RegraNegocioError):
            self.abrir()
        self.abrir(self.gestor)  # outro operador pode
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="pdv", acao="criar", tabela="pdv_caixa").exists())

    def test_esperado_sangria_suprimento_e_fechamento(self):
        caixa = self.abrir()
        self.vender(caixa)  # R$ 10,00 em dinheiro
        s.registrar_movimento(self.req_g, caixa, MovimentoCaixa.SUPRIMENTO, 5000, "troco")
        s.registrar_movimento(self.req_g, caixa, MovimentoCaixa.SANGRIA, 3000, "cofre")
        esperado = s.dinheiro_esperado(caixa)
        self.assertEqual((esperado["esperado"], esperado["vendas_dinheiro"]), (10000 + 1000 + 5000 - 3000, 1000))
        with self.assertRaises(RegraNegocioError):
            s.registrar_movimento(self.req_g, caixa, MovimentoCaixa.SANGRIA, 10**8, "demais")
        fechado = s.fechar_caixa(self.req_o, caixa, 12900, "faltou")
        self.assertEqual((fechado.valor_esperado, fechado.diferenca, fechado.status), (13000, -100, "fechado"))
        with self.assertRaises(RegraNegocioError):
            s.fechar_caixa(self.req_o, caixa, 1)
        with self.assertRaises(RegraNegocioError):
            s.registrar_movimento(self.req_g, caixa, MovimentoCaixa.SANGRIA, 100, "x")
        s.conferir_caixa(self.req_g, fechado, "ok")
        with self.assertRaises(RegraNegocioError):
            s.conferir_caixa(self.req_g, fechado)

    def test_movimento_e_imutavel(self):
        from web_project.historico import HistoricoImutavelError

        caixa = self.abrir()
        m = s.registrar_movimento(self.req_g, caixa, MovimentoCaixa.SUPRIMENTO, 100, "x")
        m.valor = 5
        with self.assertRaises(HistoricoImutavelError):
            m.save()
        with self.assertRaises(HistoricoImutavelError):
            MovimentoCaixa.objects.all().delete()

    def test_comanda_aberta_impede_fechar(self):
        caixa = self.abrir()
        mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 1")
        s.abrir_comanda(self.req_o, caixa, mesa)
        with self.assertRaises(RegraNegocioError):
            s.fechar_caixa(self.req_o, caixa, 10000)


class VendaTests(PdvBase):
    def setUp(self):
        super().setUp()
        self.caixa = self.abrir()

    def test_preco_do_servidor_baixa_de_estoque_e_rastreabilidade(self):
        venda, criada = self.vender(self.caixa, qtd=2)
        self.assertTrue(criada)
        item = venda.itens.get()
        self.assertEqual((item.preco_unitario, item.total, venda.total, venda.status, venda.numero), (500, 1000, 1000, "paga", 1))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))  # 2 × 250 g
        baixa = VendaItemInsumo.objects.get()
        self.assertEqual((baixa.insumo_id, baixa.quantidade), (self.farinha.pk, D("500.0000")))
        self.assertTrue(MovimentoEstoque.objects.filter(insumo=self.farinha, observacao="Venda PDV #1").exists())
        self.assertIsNotNone(venda.baixa_estoque_em)

    def test_preco_enviado_pelo_cliente_e_ignorado(self):
        venda, _ = s.registrar_venda(self.req_o, self.caixa, [{"produto": self.bolo.pk, "quantidade": 1, "preco": 1, "total": 1}],
                                     [{"forma": self.dinheiro.pk, "valor": 500}])
        self.assertEqual(venda.total, 500)

    def test_pagamentos_devem_somar_o_total(self):
        for valor in (499, 501):
            with self.assertRaises(RegraNegocioError):
                self.vender(self.caixa, qtd=1, pagamentos=[{"forma": self.dinheiro.pk, "valor": valor}])
        self.assertFalse(Venda.objects.filter(status="paga").exists())
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))

    def test_pagamento_misto_taxa_troco_e_snapshot(self):
        venda, _ = self.vender(self.caixa, qtd=4, pagamentos=[
            {"forma": self.dinheiro.pk, "valor": 1000, "valor_recebido": 1500}, {"forma": self.credito.pk, "valor": 1000, "pagador": "Ana"}])
        self.assertEqual(venda.troco, 500)
        cartao = venda.pagamentos.get(forma_tipo="credito")
        self.assertEqual((cartao.taxa_valor, cartao.pagador), (80, "Ana"))  # 3% de 1000 = 30 + 50 fixa
        self.credito.taxa_percentual = D("10")
        self.credito.save()
        cartao.refresh_from_db()
        self.assertEqual(cartao.taxa_valor, 80)  # alterar a forma não muda a venda feita
        self.assertEqual(venda.pagamentos.get(forma_tipo="dinheiro").valor_recebido, 1500)

    def test_valor_recebido_so_em_dinheiro_e_nao_menor_que_o_valor(self):
        with self.assertRaises(RegraNegocioError):
            self.vender(self.caixa, qtd=1, pagamentos=[{"forma": self.pix.pk, "valor": 500, "valor_recebido": 600}])
        with self.assertRaises(RegraNegocioError):
            self.vender(self.caixa, qtd=1, pagamentos=[{"forma": self.dinheiro.pk, "valor": 500, "valor_recebido": 400}])

    def test_forma_de_outra_empresa_inativa_ou_invalida(self):
        alheia = FormaPagamento.objects.create(cliente=self.outro, nome="PIX B", tipo="pix")
        self.pix.ativo = False
        self.pix.save()
        for forma in (alheia.pk, self.pix.pk, 99999, "x"):
            with self.assertRaises(RegraNegocioError):
                self.vender(self.caixa, qtd=1, pagamentos=[{"forma": forma, "valor": 500}])

    def test_conta_a_receber_ja_baixada(self):
        venda, _ = self.vender(self.caixa, qtd=2, pagamentos=[{"forma": self.dinheiro.pk, "valor": 400}, {"forma": self.pix.pk, "valor": 600}])
        conta = ContaReceber.objects.get()
        self.assertEqual((conta.valor, conta.valor_baixado, conta.status, conta.categoria.nome), (1000, 1000, "paga", "Vendas PDV"))
        self.assertEqual(conta.baixas.count(), 2)
        self.assertEqual(venda.conta_receber_id, conta.pk)

    def test_estoque_insuficiente_desfaz_tudo(self):
        pequeno = novo_insumo(self.cliente, "Ovo", "un", "1", 100)
        produto = criar_produto(self.cliente, "Omelete")
        ficha = criar_ficha(self.cliente, "Omelete", [(pequeno, "un", "3")], prod=produto)
        nova_precificacao(self.cliente, ficha, usar_no_pdv=True)
        registrar_entrada(self.gestor, pequeno, D("5"), medida("un"))
        antes = Venda.objects.count()
        total = int(Precificacao.objects.get(produto=produto).preco_venda) * 2  # 2 omeletes = 6 ovos, só há 5
        with self.assertRaises(RegraNegocioError) as erro:
            s.registrar_venda(self.req_o, self.caixa, [{"produto": produto.pk, "quantidade": 2}], [{"forma": self.dinheiro.pk, "valor": total}])
        self.assertIn("Omelete", str(erro.exception))
        self.assertEqual((Venda.objects.count(), ContaReceber.objects.count(), saldo_do_insumo(pequeno)), (antes, 0, D("5")))
        self.assertFalse(VendaItemInsumo.objects.exists())

    def test_sem_controle_de_estoque_nao_baixa(self):
        config = ConfiguracaoPdv.da_unidade(self.cliente)
        config.controla_estoque = False
        config.save()
        self.vender(self.caixa, qtd=1)
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))
        self.assertFalse(VendaItemInsumo.objects.exists())

    def test_idempotencia_nao_duplica_nada(self):
        v1, c1 = self.vender(self.caixa, qtd=1, chave="abc-123")
        v2, c2 = self.vender(self.caixa, qtd=1, chave="abc-123")
        self.assertEqual((c1, c2, v1.pk), (True, False, v2.pk))
        self.assertEqual((Venda.objects.filter(status="paga").count(), ContaReceber.objects.count(), saldo_do_insumo(self.farinha)), (1, 1, D("9.75")))

    def test_numeracao_sequencial_por_unidade(self):
        a, _ = self.vender(self.caixa, qtd=1)
        b, _ = self.vender(self.caixa, qtd=1)
        self.assertEqual((a.numero, b.numero), (1, 2))

    def test_desconto_exige_permissao_e_respeita_limites(self):
        with self.assertRaises(RegraNegocioError):
            self.vender(self.caixa, qtd=2, pagamentos=[{"forma": self.dinheiro.pk, "valor": 900}], desconto=100)
        venda, _ = self.vender(self.caixa, qtd=2, pagamentos=[{"forma": self.dinheiro.pk, "valor": 900}], desconto=100, pode_desconto=True)
        self.assertEqual((venda.subtotal, venda.desconto, venda.total), (1000, 100, 900))
        for desconto in (2000, 1000, -5):
            with self.assertRaises(RegraNegocioError):
                self.vender(self.caixa, qtd=2, pagamentos=[{"forma": self.dinheiro.pk, "valor": 1}], desconto=desconto, pode_desconto=True)

    def test_caixa_de_outro_operador_ou_fechado(self):
        with self.assertRaises(RegraNegocioError):
            s.registrar_venda(self.req_g, self.caixa, [{"produto": self.bolo.pk, "quantidade": 1}], [{"forma": self.dinheiro.pk, "valor": 500}])
        s.fechar_caixa(self.req_o, self.caixa, 10000)
        with self.assertRaises(RegraNegocioError):
            self.vender(self.caixa, qtd=1)

    def test_produto_sem_preco_do_pdv_ou_de_outra_empresa(self):
        outro_produto = criar_produto(self.outro, "Alheio")
        sem_pdv = criar_produto(self.cliente, "Sem preço")
        criar_ficha(self.cliente, "F sem pdv", [(self.farinha, "g", "10")], prod=sem_pdv)
        for pk in (outro_produto.pk, sem_pdv.pk, 99999):
            with self.assertRaises(RegraNegocioError):
                s.registrar_venda(self.req_o, self.caixa, [{"produto": pk, "quantidade": 1}], [{"forma": self.dinheiro.pk, "valor": 1}])

    def test_quantidades_invalidas(self):
        for q in (0, -1, "abc", 10000, "0.0001"):
            with self.assertRaises(RegraNegocioError, msg=q):
                self.vender(self.caixa, qtd=q)

    def test_consumidor_de_outra_unidade_e_recusado(self):
        alheio = Consumidor.objects.create(cliente=self.outro, nome="Alheio")
        with self.assertRaises(RegraNegocioError):
            self.vender(self.caixa, qtd=1, consumidor=alheio)
        meu = Consumidor.objects.create(cliente=self.cliente, nome="Maria")
        venda, _ = self.vender(self.caixa, qtd=1, consumidor=meu)
        self.assertEqual(venda.conta_receber.contraparte, "Maria")

    def test_auditoria_da_venda(self):
        venda, _ = self.vender(self.caixa, qtd=1)
        r = RegistroAuditoria.objects.filter(modulo="pdv", acao="criar", tabela="pdv_venda").latest("id")
        self.assertIn("Itens", r.descricao)
        self.assertIn("Pagamentos", r.descricao)
        self.assertEqual(r.registro_id, str(venda.pk))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="financeiro", acao="criar", registro_nome__contains="Venda PDV").exists())


class ComandaTests(PdvBase):
    def setUp(self):
        super().setUp()
        self.caixa = self.abrir()
        self.mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 7")

    def test_fluxo_completo_com_conta_dividida(self):
        venda = s.abrir_comanda(self.req_o, self.caixa, self.mesa)
        s.adicionar_item(self.req_o, venda, self.bolo.pk, 2)
        s.adicionar_item(self.req_o, venda, self.bolo.pk, 1)  # soma na mesma linha
        venda.refresh_from_db()
        self.assertEqual((venda.itens.count(), venda.subtotal, venda.status), (1, 1500, "aberta"))
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))  # nada baixado até receber
        self.assertEqual(s.dividir_em_partes(1000, 3), [334, 333, 333])
        partes = s.dividir_em_partes(1500, 3)
        pagos, criada = s.pagar_comanda(self.req_o, venda, [
            {"forma": self.dinheiro.pk, "valor": partes[0], "pagador": "A"}, {"forma": self.pix.pk, "valor": partes[1], "pagador": "B"}, {"forma": self.credito.pk, "valor": partes[2], "pagador": "C"}])
        self.assertTrue(criada)
        self.assertEqual((pagos.status, pagos.pagamentos.count(), saldo_do_insumo(self.farinha)), ("paga", 3, D("9.25")))
        with self.assertRaises(RegraNegocioError):
            s.adicionar_item(self.req_o, pagos, self.bolo.pk, 1)  # paga não aceita itens

    def test_uma_comanda_aberta_por_mesa(self):
        s.abrir_comanda(self.req_o, self.caixa, self.mesa)
        with self.assertRaises(RegraNegocioError):
            s.abrir_comanda(self.req_o, self.caixa, self.mesa)

    def test_mesa_de_outra_unidade_ou_inativa(self):
        alheia = Mesa.objects.create(cliente=self.outro, nome="X")
        inativa = Mesa.objects.create(cliente=self.cliente, nome="Y", ativo=False)
        for mesa in (alheia, inativa):
            with self.assertRaises(RegraNegocioError):
                s.abrir_comanda(self.req_o, self.caixa, mesa)

    def test_remover_item_e_pagar_vazia(self):
        venda = s.abrir_comanda(self.req_o, self.caixa, self.mesa)
        with self.assertRaises(RegraNegocioError):
            s.pagar_comanda(self.req_o, venda, [{"forma": self.dinheiro.pk, "valor": 1}])
        s.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        s.remover_item(self.req_o, venda, venda.itens.get().pk)
        self.assertEqual(Venda.objects.get(pk=venda.pk).subtotal, 0)

    def test_pagar_duas_vezes_com_a_mesma_chave_nao_duplica(self):
        venda = s.abrir_comanda(self.req_o, self.caixa, self.mesa)
        s.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        pago, c1 = s.pagar_comanda(self.req_o, venda, [{"forma": self.pix.pk, "valor": 500}], chave="k1")
        _, c2 = s.pagar_comanda(self.req_o, venda, [{"forma": self.pix.pk, "valor": 500}], chave="k1")
        self.assertEqual((c1, c2, ContaReceber.objects.count()), (True, False, 1))
        with self.assertRaises(RegraNegocioError):
            s.pagar_comanda(self.req_o, venda, [{"forma": self.pix.pk, "valor": 500}], chave="outra")


class CancelamentoEDevolucaoTests(PdvBase):
    def setUp(self):
        super().setUp()
        self.caixa = self.abrir()
        self.venda, _ = self.vender(self.caixa, qtd=4, pagamentos=[{"forma": self.dinheiro.pk, "valor": 2000}])
        self.item = self.venda.itens.get()

    def test_cancelar_repoe_estoque_estorna_e_inativa_conta(self):
        self.assertEqual(saldo_do_insumo(self.farinha), D("9"))
        s.cancelar_venda(self.req_g, self.venda, "cliente desistiu")
        self.venda.refresh_from_db()
        self.assertEqual((self.venda.status, saldo_do_insumo(self.farinha)), ("cancelada", D("10")))
        conta = ContaReceber.objects.get()
        self.assertEqual((conta.ativo, conta.valor_baixado), (False, 0))
        self.assertEqual(s.dinheiro_esperado(self.caixa)["vendas_dinheiro"], 0)
        with self.assertRaises(RegraNegocioError):
            s.cancelar_venda(self.req_g, self.venda, "de novo")
        r = RegistroAuditoria.objects.filter(modulo="pdv", acao="editar", registro_id=str(self.venda.pk)).latest("id")
        self.assertIn("cliente desistiu", r.descricao)

    def test_cancelar_nao_repoe_item_ja_preparado(self):
        VendaItem.objects.filter(pk=self.item.pk).update(preparado=True)
        s.cancelar_venda(self.req_g, self.venda, "erro")
        self.assertEqual(saldo_do_insumo(self.farinha), D("9"))  # preparado: o insumo foi consumido de fato

    def test_cancelar_exige_motivo_e_caixa_aberto(self):
        with self.assertRaises(RegraNegocioError):
            s.cancelar_venda(self.req_g, self.venda, "  ")
        s.fechar_caixa(self.req_o, self.caixa, 12000)
        with self.assertRaises(RegraNegocioError):
            s.cancelar_venda(self.req_g, self.venda, "tarde")

    def test_devolucao_parcial_valor_estoque_caixa_e_financeiro(self):
        if not Caixa.objects.filter(operador=self.gestor, status="aberto").exists():
            self.abrir(self.gestor, 10000)
        d = s.devolver_item(self.req_g, self.item, D("1"), "veio queimado", self.dinheiro, repor_estoque=True)
        self.assertEqual((d.valor, d.repor_estoque), (500, True))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.25"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantidade_devolvida, D("1"))
        pagar = ContaPagar.objects.get()
        self.assertEqual((pagar.valor, pagar.valor_baixado, pagar.categoria.nome), (500, 500, "Devoluções de vendas"))
        self.assertEqual(s.dinheiro_esperado(Caixa.objects.get(operador=self.gestor, status="aberto"))["devolucoes"], 500)

    def test_devolucao_com_desconto_rateia(self):
        venda, _ = self.vender(self.caixa, qtd=2, pagamentos=[{"forma": self.pix.pk, "valor": 900}], desconto=100, pode_desconto=True)
        item = venda.itens.get()
        d = s.devolver_item(self.req_g, item, D("1"), "x", self.pix)
        self.assertEqual(d.valor, 450)  # metade de 900

    def test_devolucao_nao_repoe_item_preparado_e_valida_limites(self):
        VendaItem.objects.filter(pk=self.item.pk).update(preparado=True)
        with self.assertRaises(RegraNegocioError):
            s.devolver_item(self.req_g, self.item, D("1"), "x", self.pix, repor_estoque=True)
        s.devolver_item(self.req_g, self.item, D("1"), "x", self.pix, repor_estoque=False)
        self.assertEqual(saldo_do_insumo(self.farinha), D("9"))
        for q in (D("4"), D("0"), "abc"):
            with self.assertRaises(RegraNegocioError):
                s.devolver_item(self.req_g, self.item, q, "x", self.pix)
        with self.assertRaises(RegraNegocioError):
            s.devolver_item(self.req_g, self.item, D("1"), "", self.pix)

    def test_devolucao_em_dinheiro_exige_caixa_aberto_do_usuario(self):
        with self.assertRaises(RegraNegocioError):
            s.devolver_item(self.req_g, self.item, D("1"), "x", self.dinheiro)  # gestor sem caixa
        self.assertFalse(Devolucao.objects.exists())
        self.assertFalse(ContaPagar.objects.exists())

    def test_nao_cancela_venda_com_devolucao_e_devolucao_e_imutavel(self):
        from web_project.historico import HistoricoImutavelError

        d = s.devolver_item(self.req_g, self.item, D("1"), "x", self.pix)
        with self.assertRaises(RegraNegocioError):
            s.cancelar_venda(self.req_g, self.venda, "x")
        d.valor = 1
        with self.assertRaises(HistoricoImutavelError):
            d.save()

    def test_devolver_total_nao_passa_do_valor_da_venda(self):
        total = 0
        for _ in range(4):
            total += s.devolver_item(self.req_g, self.item, D("1"), "x", self.pix).valor
        self.assertEqual(total, 2000)
        with self.assertRaises(RegraNegocioError):
            s.devolver_item(self.req_g, self.item, D("1"), "x", self.pix)


class TelasEPermissoesTests(PdvBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.operador)

    def abrir_pela_tela(self):
        return self.client.post(reverse("pdv-caixa-abrir"), {"valor_abertura": "100,00"})

    def finalizar(self, **corpo):
        dados = {"itens": [{"produto": self.bolo.pk, "quantidade": 2}], "pagamentos": [{"forma": self.dinheiro.pk, "valor": 1000, "valor_recebido": 2000}], "chave": "tela-1", **corpo}
        return self.client.post(reverse("pdv-api-finalizar"), json.dumps(dados), content_type="application/json")

    def test_operador_abre_caixa_vende_e_idempotencia_do_json(self):
        self.assertEqual(self.abrir_pela_tela().status_code, 302)
        r = self.finalizar()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["total"], r.json()["troco"]), (1000, 1000))
        r2 = self.finalizar()
        self.assertEqual((r2.status_code, r2.json()["venda"]), (200, r.json()["venda"]))
        self.assertEqual(Venda.objects.filter(status="paga").count(), 1)

    def test_json_sem_caixa_com_erro_e_sem_desconto_para_operador(self):
        self.assertEqual(self.finalizar().status_code, 409)
        self.abrir_pela_tela()
        r = self.finalizar(desconto=100, pagamentos=[{"forma": self.dinheiro.pk, "valor": 900}], chave="x")
        self.assertEqual(r.status_code, 422)
        self.assertIn("desconto", r.json()["erro"].lower())
        self.assertEqual(self.client.post(reverse("pdv-api-finalizar"), "nao-e-json", content_type="application/json").status_code, 422)
        self.assertEqual(self.client.post(reverse("pdv-api-finalizar"), "x" * 70000, content_type="application/json").status_code, 422)

    def test_operador_so_vende(self):
        self.abrir_pela_tela()
        r = self.finalizar()
        venda = Venda.objects.get(pk=r.json()["venda"])
        item = venda.itens.get()
        self.assertEqual(self.client.post(reverse("pdv-venda-cancelar", args=[venda.pk]), {"motivo": "x"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("pdv-venda-devolver", args=[venda.pk, item.pk]), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse("pdv-caixa-sangria"), {"valor": "1", "motivo": "x"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("pdv-caixa-suprimento"), {"valor": "1", "motivo": "x"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("pdv-caixa-conferir", args=[venda.caixa_id])).status_code, 403)
        for nome in ("forma-pagamento-list", "mesa-list", "pdv-config"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
        self.assertEqual(self.client.get(reverse("pdv-caixa")).status_code, 200)
        self.assertEqual(self.client.get(reverse("pdv-venda", args=[venda.pk])).status_code, 200)

    def test_operador_ve_so_as_proprias_vendas_e_gestor_ve_todas(self):
        self.abrir_pela_tela()
        self.finalizar()
        self.client.force_login(self.gestor)
        caixa_g = s.abrir_caixa(self.req_g, 0)
        s.registrar_venda(self.req_g, caixa_g, [{"produto": self.bolo.pk, "quantidade": 1}], [{"forma": self.pix.pk, "valor": 500}])
        gestor_ve = {v.pk for v in Venda.objects.all()}
        self.assertEqual(len(gestor_ve), 2)
        pagina = self.client.get(reverse("pdv-vendas")).content.decode()
        self.assertIn("#1", pagina)
        self.assertIn("#2", pagina)
        self.client.force_login(self.operador)
        pagina_op = self.client.get(reverse("pdv-vendas")).content.decode()
        self.assertIn("#1", pagina_op)
        self.assertNotIn("#2", pagina_op)
        v2 = Venda.objects.get(numero=2)
        self.assertEqual(self.client.get(reverse("pdv-venda", args=[v2.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("pdv-comprovante", args=[v2.pk])).status_code, 404)

    def test_outro_tenant_nao_acessa(self):
        caixa = self.abrir()
        venda, _ = self.vender(caixa, qtd=1)
        self.client.force_login(self.gestor_b)
        for nome, args in (("pdv-venda", [venda.pk]), ("pdv-comprovante", [venda.pk]), ("pdv-caixa-detalhe", [caixa.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 404, nome)
        self.assertEqual(self.client.post(reverse("pdv-venda-cancelar", args=[venda.pk]), {"motivo": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("pdv-caixa-conferir", args=[caixa.pk])).status_code, 404)
        pagina = self.client.get(reverse("pdv-vendas")).content.decode()
        self.assertNotIn(f"/pdv/vendas/{venda.pk}", pagina)

    def test_produtos_json_so_da_unidade(self):
        self.abrir_pela_tela()
        itens = self.client.get(reverse("pdv-api-produtos")).json()["itens"]
        self.assertEqual([(i["nome"], i["preco"]) for i in itens], [("Bolo", 500)])
        outro_produto = criar_produto(self.outro, "Alheio")
        f = criar_ficha(self.outro, "F alheia", [(novo_insumo(self.outro, "Sal", "kg", "1", 100), "g", "5")], prod=outro_produto)
        nova_precificacao(self.outro, f, usar_no_pdv=True)
        nomes = [i["nome"] for i in self.client.get(reverse("pdv-api-produtos")).json()["itens"]]
        self.assertNotIn("Alheio", nomes)
        self.assertEqual(self.client.get(reverse("pdv-api-produtos"), {"q": "zzz"}).json()["itens"], [])

    def test_comanda_pela_tela(self):
        self.abrir_pela_tela()
        mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 3")
        r = self.client.post(reverse("pdv-comanda-abrir"), {"mesa": mesa.pk, "observacao": ""})
        venda = Venda.objects.get(mesa=mesa)
        self.assertRedirects(r, reverse("pdv-comanda", args=[venda.pk]), fetch_redirect_response=False)
        self.client.post(reverse("pdv-comanda-itens", args=[venda.pk]), {"produto": self.bolo.pk, "quantidade": "2"})
        self.assertEqual(Venda.objects.get(pk=venda.pk).subtotal, 1000)
        pagina = self.client.get(reverse("pdv-comanda", args=[venda.pk])).content.decode()
        self.assertIn("Receber", pagina)
        r = self.client.post(reverse("pdv-comanda-pagar", args=[venda.pk]), json.dumps({"pagamentos": [{"forma": self.pix.pk, "valor": 1000}], "chave": "c1"}), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Venda.objects.get(pk=venda.pk).status, "paga")

    def test_comprovante_html_pdf_escpos(self):
        self.abrir_pela_tela()
        r = self.finalizar()
        pk = r.json()["venda"]
        html = self.client.get(reverse("pdv-comprovante", args=[pk])).content.decode()
        self.assertIn("COMPROVANTE NAO FISCAL", html)
        self.assertIn("Bolo", html)
        self.assertIn("R$ 10,00", html)
        for tamanho in ("termica80", "termica58", "a4"):
            pdf = self.client.get(reverse("pdv-comprovante", args=[pk]), {"formato": "pdf", "tamanho": tamanho})
            self.assertTrue(pdf.content.startswith(b"%PDF"), tamanho)
        raw = self.client.get(reverse("pdv-comprovante", args=[pk]), {"formato": "escpos", "tamanho": "termica58"})
        self.assertTrue(raw.content.startswith(b"\x1b@"))
        self.assertIn(b"COMPROVANTE NAO FISCAL", raw.content)
        self.assertTrue(raw.content.endswith(b"\x1d\x56\x42\x00"))
        self.assertEqual(self.client.get(reverse("pdv-comprovante", args=[pk]), {"formato": "exe"}).status_code, 404)

    def test_gestor_cancela_devolve_e_confere_pela_tela(self):
        self.abrir_pela_tela()
        pk = self.finalizar().json()["venda"]
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.post(reverse("pdv-venda-cancelar", args=[pk]), {"motivo": "teste"}).status_code, 302)
        self.assertEqual(Venda.objects.get(pk=pk).status, "cancelada")
        s.fechar_caixa(self.req_o, Caixa.objects.get(operador=self.operador), 10000)
        caixa = Caixa.objects.get(operador=self.operador)
        self.assertEqual(self.client.post(reverse("pdv-caixa-conferir", args=[caixa.pk]), {"observacao": "ok"}).status_code, 302)
        self.assertIsNotNone(Caixa.objects.get(pk=caixa.pk).conferido_em)


class LeitorDeCodigoTests(PdvBase):
    """Leitor de código de barras/QR: o código lido encontra o produto do PDV e lança na comanda."""

    def test_busca_por_codigo_e_json(self):
        self.assertTrue(self.bolo.codigo_barras)
        achado = s.produto_por_codigo(self.cliente, self.bolo.codigo_barras)
        self.assertEqual(achado.produto_id, self.bolo.pk)
        self.assertIsNone(s.produto_por_codigo(self.cliente, "0000000000000"))
        self.assertIsNone(s.produto_por_codigo(self.outro, self.bolo.codigo_barras))  # outra unidade
        self.abrir()
        self.client.force_login(self.operador)
        dados = self.client.get(reverse("pdv-api-produtos"), {"q": self.bolo.codigo_barras}).json()
        self.assertEqual([i["codigo"] for i in dados["itens"]], [self.bolo.codigo_barras])

    def test_comanda_adiciona_item_pelo_codigo(self):
        caixa = self.abrir()
        mesa = Mesa.objects.create(cliente=self.cliente, nome="Mesa 1")
        venda = s.abrir_comanda(self.req_o, caixa, mesa)
        self.client.force_login(self.operador)
        url = reverse("pdv-comanda-itens", args=[venda.pk])
        self.client.post(url, {"codigo": self.bolo.codigo_barras})
        self.client.post(url, {"codigo": self.bolo.codigo_barras})
        venda.refresh_from_db()
        self.assertEqual(venda.itens.get().quantidade, D("2"))
        resposta = self.client.post(url, {"codigo": "inexistente"}, follow=True)
        self.assertContains(resposta, "Nenhum produto com este código")
