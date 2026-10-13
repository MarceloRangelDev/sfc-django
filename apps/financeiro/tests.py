from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cliente.models import Cliente
from apps.estoque.services import registrar_entrada
from apps.insumo.tests import medida, novo_insumo
from apps.notificacao.models import DestinatarioNotificacao, Notificacao
from web_project.historico import HistoricoImutavelError
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from .models import AlertaFinanceiro, BaixaBase, BaixaPagar, BaixaReceber, CategoriaFinanceira, ContaPagar, ContaReceber
from .services import balanco, dre, estornar_baixa, gerar_alertas, registrar_baixa

HOJE = timezone.localdate()
D = timedelta


def dia(n):
    return HOJE + timedelta(days=n)


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in ("financeiro-pagar", "financeiro-receber", "financeiro-relatorios", "estoque", "insumo"):
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, pagar=("view", "add", "change", "delete"), receber=("view", "add", "change", "delete"), cat=("view", "add", "change", "delete"),
            relatorio=True, filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    codigos = [f"{a}_contapagar" for a in pagar] + [f"{a}_contareceber" for a in receber] + [f"{a}_categoriafinanceira" for a in cat]
    if relatorio:
        codigos.append("view_relatoriofinanceiro")
    perms = list(Permission.objects.filter(content_type__app_label="financeiro", codename__in=codigos))
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


def categoria(c, nome, tipo):
    return CategoriaFinanceira.objects.get_or_create(cliente=c, nome=nome, tipo=tipo)[0]


def pagar(c, valor=10000, venc=0, descricao="Aluguel", cat=None, **kw):
    return ContaPagar.objects.create(
        cliente=c, descricao=descricao, valor=valor, vencimento=dia(venc), categoria=cat or categoria(c, "Aluguel", "despesa"),
        emissao=kw.pop("emissao", dia(-30)), **kw,
    )


def receber(c, valor=10000, venc=0, descricao="Venda", cat=None, **kw):
    return ContaReceber.objects.create(
        cliente=c, descricao=descricao, valor=valor, vencimento=dia(venc), categoria=cat or categoria(c, "Vendas", "receita"),
        emissao=kw.pop("emissao", dia(-30)), **kw,
    )


class BaixasTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("u", self.c)
        self.conta = pagar(self.c, 10000)

    def test_baixa_parcial_e_total_mudam_a_situacao(self):
        self.assertEqual(self.conta.status, "aberta")
        registrar_baixa(self.user, self.conta, 4000, observacao="1ª parcela")
        self.conta.refresh_from_db()
        self.assertEqual((self.conta.valor_baixado, self.conta.saldo, self.conta.status), (4000, 6000, "parcial"))
        registrar_baixa(self.user, self.conta, 6000)
        self.conta.refresh_from_db()
        self.assertEqual((self.conta.saldo, self.conta.status), (0, "paga"))
        baixa = BaixaPagar.objects.order_by("pk").first()
        self.assertEqual((baixa.tipo, baixa.valor, baixa.responsavel_nome, baixa.data), ("baixa", 4000, "u", HOJE))

    def test_baixa_invalida_nao_grava(self):
        casos = [(0, HOJE), (-5, HOJE), (10001, HOJE), (100, dia(1))]
        for valor, data in casos:
            with self.assertRaises(RegraNegocioError):
                registrar_baixa(self.user, self.conta, valor, data)
        self.assertFalse(BaixaPagar.objects.exists())
        self.conta.ativo = False
        self.conta.save()
        with self.assertRaises(RegraNegocioError):
            registrar_baixa(self.user, self.conta, 100)

    def test_segunda_baixa_nao_passa_do_saldo(self):
        registrar_baixa(self.user, self.conta, 7000)
        with self.assertRaises(RegraNegocioError) as ctx:
            registrar_baixa(self.user, self.conta, 3001)  # a conta é relida sob bloqueio: vê o saldo real
        self.assertIn("R$ 30,00", str(ctx.exception))

    def test_banco_recusa_baixado_maior_que_o_valor(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContaPagar.objects.filter(pk=self.conta.pk).update(valor_baixado=10001)

    def test_estorno_desfaz_sem_apagar_historico(self):
        baixa = registrar_baixa(self.user, self.conta, 10000)
        estorno = estornar_baixa(self.user, baixa)
        self.conta.refresh_from_db()
        self.assertEqual((self.conta.valor_baixado, self.conta.status), (0, "aberta"))
        self.assertEqual((estorno.tipo, estorno.valor, estorno.estorno_de_id), ("estorno", 10000, baixa.pk))
        self.assertEqual(BaixaPagar.objects.count(), 2)  # a baixa original continua no histórico

    def test_estorno_so_uma_vez_e_so_de_baixa(self):
        baixa = registrar_baixa(self.user, self.conta, 3000)
        estorno = estornar_baixa(self.user, baixa)
        with self.assertRaises(RegraNegocioError):
            estornar_baixa(self.user, baixa)  # já estornada
        with self.assertRaises(RegraNegocioError):
            estornar_baixa(self.user, estorno)  # estorno não se estorna

    def test_historico_de_baixas_e_imutavel(self):
        baixa = registrar_baixa(self.user, self.conta, 100)
        baixa.valor = 1
        with self.assertRaises(HistoricoImutavelError):
            baixa.save()
        with self.assertRaises(HistoricoImutavelError):
            baixa.delete()
        with self.assertRaises(HistoricoImutavelError):
            BaixaPagar.objects.all().update(valor=1)
        with self.assertRaises(HistoricoImutavelError):
            BaixaPagar.objects.all().delete()

    def test_atrasada_so_com_saldo_e_vencimento_passado(self):
        atrasada = pagar(self.c, 1000, venc=-1, descricao="Velha")
        self.assertTrue(atrasada.atrasada)
        registrar_baixa(self.user, atrasada, 1000)
        atrasada.refresh_from_db()
        self.assertFalse(atrasada.atrasada)  # paga não é atrasada
        self.assertFalse(pagar(self.c, 1000, venc=0, descricao="Hoje").atrasada)  # vence hoje: ainda não


class RelatoriosTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("u", self.c)

    def baixar(self, conta, valor, data):
        return registrar_baixa(self.user, conta, valor, data)

    def test_dre_por_caixa_agrupa_por_categoria_e_considera_estornos(self):
        vendas, outras = categoria(self.c, "Vendas", "receita"), categoria(self.c, "Outras", "receita")
        aluguel, luz = categoria(self.c, "Aluguel", "despesa"), categoria(self.c, "Luz", "despesa")
        self.baixar(receber(self.c, 50000, cat=vendas), 50000, dia(-2))
        self.baixar(receber(self.c, 8000, cat=outras, descricao="Outra"), 8000, dia(-1))
        self.baixar(pagar(self.c, 20000, cat=aluguel), 20000, dia(-1))
        b_luz = self.baixar(pagar(self.c, 3000, cat=luz, descricao="Luz"), 3000, dia(-1))
        estornar_baixa(self.user, b_luz)  # estorno hoje: o pagamento da luz deixa de contar
        self.baixar(receber(self.c, 9999, cat=vendas, descricao="Fora"), 9999, dia(-40))  # fora do período
        r = dre(self.c, dia(-7), dia(0))
        self.assertEqual(r["receitas"], [("Outras", 8000), ("Vendas", 50000)])
        self.assertEqual(r["despesas"], [("Aluguel", 20000)])  # luz: pagamento (-1) e estorno (hoje) se anulam
        self.assertEqual((r["total_receitas"], r["total_despesas"], r["resultado"]), (58000, 20000, 38000))

    def test_dre_por_vencimento_usa_o_valor_das_contas_ativas(self):
        receber(self.c, 5000, venc=3)
        receber(self.c, 1000, venc=3, descricao="Cancelada", ativo=False)
        pagar(self.c, 2000, venc=5)
        pagar(self.c, 9999, venc=60, descricao="Fora")
        r = dre(self.c, dia(0), dia(10), "vencimento")
        self.assertEqual((r["total_receitas"], r["total_despesas"], r["resultado"]), (5000, 2000, 3000))

    def test_dre_periodo_invalido_e_isolamento_por_unidade(self):
        with self.assertRaises(RegraNegocioError):
            dre(self.c, dia(1), dia(0))
        outra = cliente("Outra", "34028316000103")
        self.baixar(receber(outra, 777), 777, dia(0))
        self.assertEqual(dre(self.c, dia(-1), dia(1))["total_receitas"], 0)

    def test_balanco(self):
        r1 = receber(self.c, 10000)  # recebe 6000 -> sobra 4000 a receber
        self.baixar(r1, 6000, dia(-1))
        p1 = pagar(self.c, 5000)  # paga 2000 -> sobra 3000 a pagar
        self.baixar(p1, 2000, dia(-1))
        insumo = novo_insumo(self.c, "Farinha", "kg", "1", 1000)  # R$ 10,00 / kg
        registrar_entrada(self.user, insumo, Decimal("3"), medida("kg"))  # 3 kg a custo = R$ 30,00
        b = balanco(self.c, HOJE)
        self.assertEqual((b["caixa"], b["a_receber"], b["estoque"]), (4000, 4000, 3000))
        self.assertEqual((b["ativo"], b["passivo"], b["patrimonio"]), (11000, 3000, 8000))

    def test_balanco_em_data_passada_e_cancelada_nao_conta(self):
        r1 = receber(self.c, 10000, emissao=dia(-10))
        self.baixar(r1, 10000, dia(-1))
        receber(self.c, 5000, descricao="Cancelada", ativo=False)
        receber(self.c, 7000, descricao="Futura", emissao=dia(5))
        antes = balanco(self.c, dia(-5))
        self.assertEqual((antes["caixa"], antes["a_receber"]), (0, 10000))  # a baixa ainda não tinha acontecido
        hoje = balanco(self.c, HOJE)
        self.assertEqual((hoje["caixa"], hoje["a_receber"]), (10000, 0))

    def test_caixa_pode_ficar_negativo_sem_saldo_inicial(self):
        self.baixar(pagar(self.c, 3000), 3000, dia(0))
        b = balanco(self.c, HOJE)
        self.assertEqual((b["caixa"], b["patrimonio"]), (-3000, -3000))


class AlertasTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.gestor = usuario("gestor", self.c)
        self.so_pagar = usuario("sopagar", self.c, receber=())
        self.atrasada = pagar(self.c, 1000, venc=-2, descricao="Atrasada")
        self.vencendo = pagar(self.c, 2000, venc=2, descricao="Vencendo")
        pagar(self.c, 3000, venc=10, descricao="Longe")
        paga = pagar(self.c, 4000, venc=-5, descricao="Paga")
        registrar_baixa(self.gestor, paga, 4000)
        receber(self.c, 5000, venc=-1, descricao="Cliente atrasado")

    def test_alertas_notificam_por_tipo_so_para_quem_pode_ver(self):
        total = gerar_alertas([self.c])
        self.assertEqual(total, {"pagar_atrasada": 1, "pagar_vencendo": 1, "receber_atrasada": 1, "receber_vencendo": 0})
        titulos = sorted(Notificacao.objects.values_list("titulo", flat=True))
        self.assertEqual(titulos, ["Contas a pagar atrasadas", "Contas a pagar vencendo", "Contas a receber atrasadas"])
        quem = {t: sorted(DestinatarioNotificacao.objects.filter(notificacao__titulo=t).values_list("usuario__username", flat=True)) for t in titulos}
        self.assertEqual(quem["Contas a pagar atrasadas"], ["gestor", "sopagar"])
        self.assertEqual(quem["Contas a receber atrasadas"], ["gestor"])  # quem não vê contas a receber não é avisado
        self.assertIn("Atrasada", Notificacao.objects.get(titulo="Contas a pagar atrasadas").mensagem)

    def test_idempotente_no_mesmo_dia_e_repete_no_dia_seguinte(self):
        gerar_alertas([self.c])
        self.assertEqual(sum(gerar_alertas([self.c]).values()), 0)
        self.assertEqual(Notificacao.objects.count(), 3)
        AlertaFinanceiro.objects.update(data=HOJE - timedelta(days=1))
        self.assertEqual(sum(gerar_alertas([self.c]).values()), 3)

    def test_nao_avisa_outra_unidade_e_comando_funciona(self):
        outra = cliente("Outra", "34028316000103")
        usuario("outro", outra)
        call_command("gerar_alertas_financeiro", verbosity=0)
        call_command("gerar_alertas_financeiro", verbosity=0)
        self.assertFalse(DestinatarioNotificacao.objects.filter(usuario__username="outro").exists())
        self.assertEqual(Notificacao.objects.count(), 3)


class TelasTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.desp = categoria(self.matriz, "Aluguel", "despesa")
        self.rec = categoria(self.matriz, "Vendas", "receita")

    def dados(self, **extra):
        base = {
            "descricao": "Aluguel de outubro", "contraparte": "Imobiliária", "categoria": self.desp.pk, "valor": "1.500,00",
            "emissao": str(HOJE), "vencimento": str(dia(10)), "observacao": "",
        }
        return {**base, **extra}

    def nomes(self, chave="pagar", **p):
        return [c.descricao for c in self.client.get(reverse(f"conta-{chave}-list"), p).context["contas"]]

    def test_criar_conta_a_pagar_e_a_receber(self):
        resp = self.client.post(reverse("conta-pagar-create"), self.dados())
        self.assertRedirects(resp, reverse("conta-pagar-list"), fetch_redirect_response=False)
        conta = ContaPagar.objects.get()
        self.assertEqual((conta.cliente_id, conta.valor, conta.status), (self.matriz.pk, 150000, "aberta"))
        resp = self.client.post(reverse("conta-receber-create"), self.dados(categoria=self.rec.pk, descricao="Venda x", salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("conta-receber-create"), fetch_redirect_response=False)
        self.assertEqual(ContaReceber.objects.get().descricao, "Venda x")

    def test_validacoes_do_formulario(self):
        alheia = categoria(self.outra, "Aluguel", "despesa")
        inativa = categoria(self.matriz, "Velha", "despesa")
        inativa.ativo = False
        inativa.save()
        casos = [
            self.dados(valor="0"), self.dados(valor="-5"), self.dados(valor="abc"), self.dados(descricao=""),
            self.dados(vencimento=str(dia(-40))),  # vencimento antes da emissão
            self.dados(categoria=self.rec.pk),  # categoria de receita numa conta a pagar
            self.dados(categoria=alheia.pk), self.dados(categoria=inativa.pk),
        ]
        for dados in casos:
            self.assertEqual(self.client.post(reverse("conta-pagar-create"), dados).status_code, 200, dados)
        self.assertFalse(ContaPagar.objects.exists())

    def test_editar_nao_aceita_valor_menor_que_o_baixado_nem_conta_cancelada(self):
        conta = pagar(self.matriz, 10000, cat=self.desp)
        registrar_baixa(self.user, conta, 6000)
        url = reverse("conta-pagar-edit", args=[conta.pk])
        self.assertEqual(self.client.post(url, self.dados(valor="50,00")).status_code, 200)  # 5.000 < 6.000 baixados
        self.assertEqual(self.client.post(url, self.dados(valor="80,00")).status_code, 302)
        conta.refresh_from_db()
        self.assertEqual((conta.valor, conta.cliente_id), (8000, self.matriz.pk))
        ContaPagar.objects.filter(pk=conta.pk).update(ativo=False)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_baixar_e_estornar_pela_tela(self):
        conta = pagar(self.matriz, 10000, cat=self.desp)
        detalhe = reverse("conta-pagar-detail", args=[conta.pk])
        self.assertContains(self.client.get(detalhe), "Saldo disponível")
        resp = self.client.post(reverse("conta-pagar-baixar", args=[conta.pk]), {"valor": "40,00", "data": str(HOJE), "observacao": "pix"})
        self.assertRedirects(resp, detalhe, fetch_redirect_response=False)
        conta.refresh_from_db()
        self.assertEqual(conta.valor_baixado, 4000)
        self.client.post(reverse("conta-pagar-baixar", args=[conta.pk]), {"valor": "999,00", "data": str(HOJE)})  # excede o saldo
        self.client.post(reverse("conta-pagar-baixar", args=[conta.pk]), {"valor": "1,00", "data": str(dia(2))})  # data futura
        conta.refresh_from_db()
        self.assertEqual(conta.valor_baixado, 4000)
        baixa = conta.baixas.get()
        self.client.post(reverse("conta-pagar-estornar", args=[conta.pk, baixa.pk]))
        conta.refresh_from_db()
        self.assertEqual(conta.valor_baixado, 0)
        self.client.post(reverse("conta-pagar-estornar", args=[conta.pk, baixa.pk]))  # de novo: recusado
        self.assertEqual(conta.baixas.count(), 2)

    def test_excluir_conta(self):
        livre = pagar(self.matriz, 1000, cat=self.desp, descricao="Livre")
        self.client.post(reverse("conta-pagar-excluir", args=[livre.pk]))
        self.assertFalse(ContaPagar.objects.filter(pk=livre.pk).exists())
        com_baixa = pagar(self.matriz, 1000, cat=self.desp, descricao="Com baixa")
        baixa = registrar_baixa(self.user, com_baixa, 500)
        self.client.post(reverse("conta-pagar-excluir", args=[com_baixa.pk]))
        com_baixa.refresh_from_db()
        self.assertTrue(com_baixa.ativo)  # bloqueada: estorne antes
        estornar_baixa(self.user, baixa)
        self.client.post(reverse("conta-pagar-excluir", args=[com_baixa.pk]))
        com_baixa.refresh_from_db()
        self.assertFalse(com_baixa.ativo)  # cancelada (o histórico de baixas e estorno permanece)
        self.assertEqual(BaixaPagar.objects.count(), 2)

    def test_lista_filtros_totais_e_ordenacao(self):
        pagar(self.matriz, 10000, venc=-3, descricao="Atrasada", cat=self.desp)
        parcial = pagar(self.matriz, 20000, venc=5, descricao="Parcial", cat=self.desp)
        registrar_baixa(self.user, parcial, 5000)
        quitada = pagar(self.matriz, 30000, venc=8, descricao="Quitada", cat=self.desp)
        registrar_baixa(self.user, quitada, 30000)
        pagar(self.matriz, 1000, venc=1, descricao="Cancelada", cat=self.desp, ativo=False)
        self.assertEqual(self.nomes(), ["Atrasada", "Parcial", "Quitada"])  # sem canceladas; por vencimento
        self.assertEqual(self.nomes(sort="valor", dir="desc"), ["Quitada", "Parcial", "Atrasada"])
        self.assertEqual(self.nomes(situacao="atrasada"), ["Atrasada"])
        self.assertEqual(self.nomes(situacao="parcial"), ["Parcial"])
        self.assertEqual(self.nomes(situacao="paga"), ["Quitada"])
        self.assertEqual(self.nomes(situacao="aberta"), ["Atrasada"])
        self.assertEqual(self.nomes(situacao="cancelada"), ["Cancelada"])
        self.assertEqual(self.nomes(vencimento_de=str(dia(4)), vencimento_ate=str(dia(6))), ["Parcial"])
        self.assertEqual(self.nomes(q="parc"), ["Parcial"])
        self.assertEqual(self.nomes(categoria=self.rec.pk), [])
        ctx = self.client.get(reverse("conta-pagar-list")).context
        self.assertEqual((ctx["total_valor"], ctx["total_baixado"], ctx["total_saldo"]), ("R$ 600,00", "R$ 350,00", "R$ 250,00"))
        self.assertEqual((ctx["qtd_atrasadas"], ctx["qtd_vencendo"]), (1, 0))  # "Parcial" vence em 5 dias: fora da janela de 3

    def test_exportacao_respeita_filtro_e_escopo(self):
        pagar(self.matriz, 10000, descricao="Minha", cat=self.desp)
        pagar(self.matriz, 5000, descricao="Outra conta", cat=self.desp)
        pagar(self.outra, 777, descricao="Alheia")
        resp = self.client.get(reverse("conta-pagar-list"), {"export": "xlsx", "q": "minha"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([(l[0], l[6]) for l in linhas[1:]], [("Minha", "R$ 100,00")])
        self.assertTrue(self.client.get(reverse("conta-receber-list"), {"export": "pdf"}).content.startswith(b"%PDF"))

    def test_permissoes_por_tipo_de_conta(self):
        conta = pagar(self.matriz, 1000, cat=self.desp)
        so_ver = usuario("v", self.matriz, pagar=("view",), receber=())
        self.client.force_login(so_ver)
        self.assertEqual(self.client.get(reverse("conta-pagar-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("conta-receber-list")).status_code, 403)  # sem permissão de receber
        self.assertEqual(self.client.get(reverse("conta-pagar-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("conta-pagar-edit", args=[conta.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("conta-pagar-baixar", args=[conta.pk]), {"valor": "1", "data": str(HOJE)}).status_code, 403)
        self.assertEqual(self.client.post(reverse("conta-pagar-excluir", args=[conta.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("financeiro-dre")).status_code, 200)  # tem o relatório (helper dá por padrão)
        self.client.force_login(usuario("sem", self.matriz, pagar=(), receber=(), cat=(), relatorio=False))
        for nome in ("conta-pagar-list", "financeiro-dre", "financeiro-balanco", "financeiro-categorias"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)

    def test_tenant_e_matriz_filial(self):
        alheia = pagar(self.outra, 1000, descricao="Alheia")
        da_filial = pagar(self.filial, 1000, descricao="DaFilial")
        for nome in ("conta-pagar-edit", "conta-pagar-detail"):
            self.assertEqual(self.client.get(reverse(nome, args=[alheia.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("conta-pagar-baixar", args=[alheia.pk]), {"valor": "1", "data": str(HOJE)}).status_code, 404)
        self.assertEqual(self.client.post(reverse("conta-pagar-excluir", args=[alheia.pk])).status_code, 404)
        self.assertNotIn("DaFilial", self.nomes())  # sem permissão de filiais
        self.assertEqual(self.client.get(reverse("conta-pagar-detail", args=[da_filial.pk])).status_code, 404)
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change")))
        self.assertIn("DaFilial", self.nomes())
        self.assertEqual(self.client.get(reverse("conta-pagar-detail", args=[da_filial.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("conta-pagar-baixar", args=[da_filial.pk]), {"valor": "5,00", "data": str(HOJE)}).status_code, 302)
        da_filial.refresh_from_db()
        self.assertEqual(da_filial.valor_baixado, 500)
        self.client.force_login(usuario("f", self.filial, filiais=("view",)))
        self.assertEqual(self.nomes(), ["DaFilial"])  # a filial nunca vê a matriz

    def test_categorias_financeiras(self):
        url = reverse("financeiro-categorias")
        self.assertEqual(self.client.post(url, {"nome": "Impostos", "tipo": "despesa"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"nome": "impostos", "tipo": "despesa"}).status_code, 200)  # duplicada
        self.assertEqual(self.client.post(url, {"nome": "Impostos", "tipo": "receita"}).status_code, 302)  # outro tipo: ok
        self.client.post(reverse("financeiro-categorias-padrao"))
        self.assertTrue(CategoriaFinanceira.objects.filter(cliente=self.matriz, nome="Fornecedores").exists())
        self.client.post(reverse("financeiro-categorias-padrao"))  # não duplica
        self.assertEqual(CategoriaFinanceira.objects.filter(cliente=self.matriz, nome="Vendas").count(), 1)
        self.assertFalse(CategoriaFinanceira.objects.filter(cliente=self.filial).exists())  # nada em outras unidades
        imp = CategoriaFinanceira.objects.get(nome="Impostos", tipo="despesa")
        self.client.post(reverse("financeiro-categoria-alternar", args=[imp.pk]))
        imp.refresh_from_db()
        self.assertFalse(imp.ativo)
        pagar(self.matriz, 100, cat=self.desp)
        self.client.post(reverse("financeiro-categoria-excluir", args=[self.desp.pk]))  # em uso: só desativa
        self.desp.refresh_from_db()
        self.assertFalse(self.desp.ativo)
        self.client.post(reverse("financeiro-categoria-excluir", args=[self.rec.pk]))  # sem uso: apaga
        self.assertFalse(CategoriaFinanceira.objects.filter(pk=self.rec.pk).exists())

    def test_dre_e_balanco_pelas_telas(self):
        conta = receber(self.matriz, 50000, cat=self.rec)
        registrar_baixa(self.user, conta, 50000)
        resp = self.client.get(reverse("financeiro-dre"))
        self.assertEqual((resp.context["total_receitas"], resp.context["resultado"]), ("R$ 500,00", "R$ 500,00"))
        self.assertContains(resp, "Relatório gerencial")
        self.assertContains(resp, "Vendas")
        invertido = self.client.get(reverse("financeiro-dre"), {"inicio": str(dia(5)), "fim": str(dia(1))})
        self.assertEqual(invertido.status_code, 200)  # período inválido: aviso e cai no mês corrente
        self.assertContains(invertido, "posterior")
        self.assertEqual(self.client.get(reverse("financeiro-dre"), {"regime": "vencimento"}).context["regime"], "vencimento")
        b = self.client.get(reverse("financeiro-balanco")).context
        self.assertEqual((b["caixa"], b["ativo"], b["patrimonio"]), ("R$ 500,00", "R$ 500,00", "R$ 500,00"))
        self.assertContains(self.client.get(reverse("financeiro-balanco")), "Relatório gerencial")

    def test_relatorio_da_filial_so_com_permissao_e_nunca_mistura_unidades(self):
        registrar_baixa(self.user, receber(self.matriz, 1000, cat=self.rec), 1000)
        ctx = self.client.get(reverse("financeiro-dre"), {"unidade": self.filial.pk}).context  # sem permissão de filiais
        self.assertEqual(ctx["unidade_atual"], self.matriz)
        self.client.force_login(usuario("m", self.matriz, filiais=("view",)))
        ctx = self.client.get(reverse("financeiro-dre"), {"unidade": self.filial.pk}).context
        self.assertEqual((ctx["unidade_atual"], ctx["total_receitas"]), (self.filial, "R$ 0,00"))


class PermissoesPadraoTests(TestCase):
    def test_relatorios_so_para_administrador_e_gestor(self):
        call_command("seed_acl", verbosity=0)
        tem = lambda grupo, cod: Group.objects.get(name=grupo).permissions.filter(codename=cod).exists()
        self.assertTrue(tem("Administrador", "view_relatoriofinanceiro") and tem("Gestor", "view_relatoriofinanceiro"))
        self.assertFalse(tem("Operador", "view_relatoriofinanceiro") or tem("Visualizador", "view_relatoriofinanceiro"))
        self.assertTrue(tem("Operador", "add_contapagar") and tem("Operador", "view_contareceber"))
        self.assertFalse(tem("Operador", "change_contapagar"))
        self.assertTrue(tem("Visualizador", "view_contapagar"))
