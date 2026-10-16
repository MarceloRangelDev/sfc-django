"""Configurações do PDV: formas de pagamento (taxa percentual, fixa ou ambas), mesas, ajustes e marca "usar no PDV"."""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.ficha.tests import criar as criar_ficha
from apps.ficha.tests import produto as criar_produto
from apps.insumo.tests import novo_insumo
from apps.precificacao.models import Precificacao
from apps.precificacao.tests import nova_precificacao

from .models import ConfiguracaoPdv, FormaPagamento, Mesa

D = Decimal


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = cliente
    u.profile.save()
    u.groups.add(Group.objects.get(name=grupo))
    return u


class ConfigBase(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="A")
        self.outro = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="B")
        self.gestor = usuario("gestor", self.cliente, "Gestor")
        self.operador = usuario("operador", self.cliente, "Operador")
        self.gestor_b = usuario("gestor_b", self.outro, "Gestor")
        self.client.force_login(self.gestor)

    def criar_forma(self, **extra):
        dados = {"nome": "Crédito", "tipo": "credito", "taxa_modo": "sem_taxa", "taxa_percentual": "", "taxa_fixa": "", "ativo": "on", **extra}
        return self.client.post(reverse("forma-pagamento-create"), dados)


class FormaPagamentoTelaTests(ConfigBase):
    def test_escolhe_percentual_fixa_ou_ambas(self):
        self.criar_forma(nome="A", taxa_modo="percentual", taxa_percentual="3,5")
        self.criar_forma(nome="B", taxa_modo="fixa", taxa_fixa="0,50")
        self.criar_forma(nome="C", taxa_modo="ambas", taxa_percentual="2", taxa_fixa="1,00")
        self.criar_forma(nome="D")
        formas = {f.nome: f for f in FormaPagamento.objects.all()}
        self.assertEqual((formas["A"].taxa_percentual, formas["A"].taxa_fixa), (D("3.5"), 0))
        self.assertEqual((formas["B"].taxa_percentual, formas["B"].taxa_fixa), (D("0"), 50))
        self.assertEqual((formas["C"].taxa_percentual, formas["C"].taxa_fixa), (D("2"), 100))
        self.assertEqual(formas["D"].calcular_taxa(5000), 0)
        self.assertEqual(formas["C"].calcular_taxa(5000), 200)
        self.assertEqual(formas["C"].descricao_da_taxa, "2% + R$ 1,00 por transação")

    def test_modo_exige_valores_e_ignora_o_que_nao_usa(self):
        self.criar_forma(nome="X", taxa_modo="percentual")  # sem percentual
        self.criar_forma(nome="Z", taxa_modo="ambas", taxa_percentual="2")  # falta a fixa
        self.assertFalse(FormaPagamento.objects.exists())
        self.criar_forma(nome="W", taxa_modo="fixa", taxa_fixa="1,00", taxa_percentual="9")
        self.assertEqual(FormaPagamento.objects.get(nome="W").taxa_percentual, D("0"))
        self.criar_forma(nome="V", taxa_modo="percentual", taxa_percentual="100")  # >= 100%
        self.assertFalse(FormaPagamento.objects.filter(nome="V").exists())

    def test_unidade_do_usuario_nome_unico_e_auditoria(self):
        self.criar_forma(nome="PIX", tipo="pix", cliente=self.outro.pk)
        forma = FormaPagamento.objects.get()
        self.assertEqual(forma.cliente_id, self.cliente.pk)
        self.criar_forma(nome="pix", tipo="pix")
        self.assertEqual(FormaPagamento.objects.count(), 1)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="pdvconfig", acao="criar", registro_nome="PIX").exists())

    def test_editar_audita_taxa(self):
        self.criar_forma(nome="Cartão", taxa_modo="percentual", taxa_percentual="2")
        forma = FormaPagamento.objects.get()
        self.client.post(reverse("forma-pagamento-edit", args=[forma.pk]), {"nome": "Cartão", "tipo": "credito", "taxa_modo": "ambas", "taxa_percentual": "2", "taxa_fixa": "0,30", "ativo": "on"})
        forma.refresh_from_db()
        self.assertEqual((forma.taxa_modo, forma.taxa_fixa), ("ambas", 30))
        registro = RegistroAuditoria.objects.filter(modulo="pdvconfig", acao="editar").latest("id")
        self.assertIn("taxa_modo", {a["campo"] for a in registro.alteracoes})

    def test_operador_nao_acessa_e_outro_tenant_nao_ve(self):
        self.criar_forma(nome="PIX", tipo="pix")
        forma = FormaPagamento.objects.get()
        self.client.force_login(self.operador)
        for nome in ("forma-pagamento-list", "forma-pagamento-create", "mesa-list", "pdv-config"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
        self.assertEqual(self.client.post(reverse("forma-pagamento-padrao")).status_code, 403)
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("forma-pagamento-edit", args=[forma.pk])).status_code, 404)
        self.assertNotIn("PIX", self.client.get(reverse("forma-pagamento-list")).content.decode())

    def test_formas_padrao_e_idempotente(self):
        self.client.post(reverse("forma-pagamento-padrao"))
        self.client.post(reverse("forma-pagamento-padrao"))
        self.assertEqual(set(FormaPagamento.objects.values_list("nome", flat=True)), {"Dinheiro", "PIX", "Cartão de débito", "Cartão de crédito"})
        self.assertEqual(FormaPagamento.objects.filter(tipo="dinheiro").count(), 1)

    def _filiais(self):
        self.cliente.tipo_estabelecimento = Cliente.TIPO_MATRIZ
        self.cliente.save()
        f1 = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="52998224725", nome_razao_social="F1", tipo_estabelecimento=Cliente.TIPO_FILIAL, matriz=self.cliente)
        f2 = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000262", nome_razao_social="F2", tipo_estabelecimento=Cliente.TIPO_FILIAL, matriz=self.cliente)
        return f1, f2

    def test_formas_padrao_sem_filiais_so_a_unidade(self):
        f1, _ = self._filiais()
        self.client.post(reverse("forma-pagamento-padrao"), {"filiais": "nao"})
        self.assertEqual(FormaPagamento.objects.filter(cliente=self.cliente).count(), 4)
        self.assertFalse(FormaPagamento.objects.filter(cliente=f1).exists())

    def test_formas_padrao_todas_as_filiais(self):
        f1, f2 = self._filiais()
        resposta = self.client.get(reverse("forma-pagamento-list"))
        self.assertContains(resposta, "Deseja criar também para as filiais?")
        self.client.post(reverse("forma-pagamento-padrao"), {"filiais": "todas"})
        for unidade in (self.cliente, f1, f2):
            self.assertEqual(FormaPagamento.objects.filter(cliente=unidade).count(), 4)

    def test_formas_padrao_filiais_escolhidas_e_alheias_ignoradas(self):
        f1, f2 = self._filiais()
        self.client.post(reverse("forma-pagamento-padrao"), {"filiais": "escolher", "filial": [f2.pk, self.outro.pk]})
        self.assertEqual(FormaPagamento.objects.filter(cliente=f2).count(), 4)
        self.assertFalse(FormaPagamento.objects.filter(cliente__in=[f1, self.outro]).exists())

    def test_lista_mostra_a_taxa(self):
        self.criar_forma(nome="Cartão", taxa_modo="ambas", taxa_percentual="3", taxa_fixa="0,50")
        self.assertIn("3% + R$ 0,50 por transação", self.client.get(reverse("forma-pagamento-list")).content.decode())


class MesaEConfiguracaoTests(ConfigBase):
    def test_mesa_crud_e_unicidade(self):
        self.client.post(reverse("mesa-create"), {"nome": "Mesa 1", "ativo": "on"})
        self.client.post(reverse("mesa-create"), {"nome": "mesa 1", "ativo": "on"})
        self.assertEqual(Mesa.objects.count(), 1)
        mesa = Mesa.objects.get()
        self.client.post(reverse("mesa-edit", args=[mesa.pk]), {"nome": "Mesa 1A"})
        mesa.refresh_from_db()
        self.assertEqual((mesa.nome, mesa.ativo), ("Mesa 1A", False))
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("mesa-edit", args=[mesa.pk])).status_code, 404)

    def test_configuracao_da_propria_unidade(self):
        self.assertEqual(self.client.get(reverse("pdv-config")).status_code, 200)
        self.client.post(reverse("pdv-config"), {"formato_comprovante": "a4", "rodape_comprovante": "Volte sempre"})  # controla_estoque desmarcado
        config = ConfiguracaoPdv.objects.get(cliente=self.cliente)
        self.assertEqual((config.controla_estoque, config.formato_comprovante, config.rodape_comprovante), (False, "a4", "Volte sempre"))
        self.assertFalse(ConfiguracaoPdv.objects.filter(cliente=self.outro).exists())  # a outra unidade não foi tocada
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="pdvconfig", acao="editar").exists())


class UsarNoPdvTests(ConfigBase):
    def test_so_uma_precificacao_por_produto_e_auditada(self):
        farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        produto = criar_produto(self.cliente, "Bolo")
        ficha = criar_ficha(self.cliente, "Bolo", [(farinha, "g", "100")], prod=produto)
        a = nova_precificacao(self.cliente, ficha, usar_no_pdv=True)
        b = nova_precificacao(self.cliente, ficha, canal_nome="Outro", usar_no_pdv=True)
        a.refresh_from_db()
        self.assertEqual((a.usar_no_pdv, b.usar_no_pdv), (False, True))
        self.assertEqual(Precificacao.objects.filter(produto=produto, usar_no_pdv=True).count(), 1)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="precificacao", descricao__contains="Usar no PDV").exists())
