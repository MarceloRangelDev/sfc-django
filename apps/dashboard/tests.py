import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.estoque.services import definir_minimo, registrar_entrada
from apps.ficha.tests import criar as criar_ficha
from apps.financeiro.models import CategoriaFinanceira, ContaPagar, ContaReceber
from apps.financeiro.services import registrar_baixa
from apps.insumo.tests import medida, novo_insumo
from apps.precificacao.tests import nova_precificacao
from web_project.testing import habilitar_modulo

from .models import Presenca
from .presenca import MINUTOS_ONLINE, registrar_atividade, usuarios_online
from .services import indicadores, periodo

HOJE = timezone.localdate()
D = Decimal

PERMISSOES = [
    ("financeiro", "view_relatoriofinanceiro"), ("financeiro", "view_contapagar"), ("financeiro", "view_contareceber"),
    ("estoque", "view_movimentoestoque"), ("insumo", "view_insumo"), ("produto", "view_produto"),
    ("precificacao", "view_precificacao"), ("categoria", "view_categoria"), ("auth", "view_user"), ("dashboard", "view_painel"),
]
MODULOS = ("financeiro-pagar", "financeiro-receber", "financeiro-relatorios", "estoque", "insumo", "produto", "precificacao", "categoria", "painel")


def dia(n):
    return HOJE + timedelta(days=n)


def cliente(nome, cnpj, matriz=None, modulos=MODULOS):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in modulos:
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, permissoes=PERMISSOES, filiais=(), **extra):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = [Permission.objects.get(content_type__app_label=app, codename=cod) for app, cod in permissoes]
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!", **extra)
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


class PeriodoTests(SimpleTestCase):
    def test_presets(self):
        hoje = date(2026, 10, 9)
        self.assertEqual(periodo("mes_atual", hoje=hoje)[:3], (date(2026, 10, 1), date(2026, 10, 31), "mes_atual"))
        self.assertEqual(periodo("mes_anterior", hoje=hoje)[:2], (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(periodo("30_dias", hoje=hoje)[:2], (date(2026, 9, 10), hoje))

    def test_mes_anterior_na_virada_do_ano_e_fevereiro(self):
        self.assertEqual(periodo("mes_anterior", hoje=date(2026, 1, 15))[:2], (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual(periodo("mes_atual", hoje=date(2028, 2, 10))[:2], (date(2028, 2, 1), date(2028, 2, 29)))  # bissexto

    def test_personalizado_valido_e_invalidos_caem_no_mes_atual(self):
        hoje = date(2026, 10, 9)
        self.assertEqual(periodo("personalizado", date(2026, 8, 1), date(2026, 8, 15), hoje)[:3], (date(2026, 8, 1), date(2026, 8, 15), "personalizado"))
        for args in (("personalizado", None, None), ("personalizado", date(2026, 8, 20), date(2026, 8, 1)), ("lixo", None, None)):
            self.assertEqual(periodo(*args, hoje=hoje)[:3], (date(2026, 10, 1), date(2026, 10, 31), "mes_atual"))


class IndicadoresTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("u", self.c)
        # financeiro
        vendas = CategoriaFinanceira.objects.create(cliente=self.c, nome="Vendas", tipo="receita")
        aluguel = CategoriaFinanceira.objects.create(cliente=self.c, nome="Aluguel", tipo="despesa")
        r = ContaReceber.objects.create(cliente=self.c, descricao="Venda", categoria=vendas, valor=100000, emissao=dia(-10), vencimento=dia(-1))
        registrar_baixa(self.user, r, 60000, dia(0))  # 600 recebidos; 400 em aberto e atrasados
        p = ContaPagar.objects.create(cliente=self.c, descricao="Aluguel", categoria=aluguel, valor=30000, emissao=dia(-5), vencimento=dia(0))
        registrar_baixa(self.user, p, 10000, dia(0))  # 100 pagos; 200 em aberto, vencem hoje
        # estoque, categorias e precificação
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)
        registrar_entrada(self.user, self.farinha, D("3"), medida("kg"), "L1", dia(2))  # 3 kg = R$ 30,00; lote vence logo
        definir_minimo(None, self.farinha, D("10"))
        Categoria.objects.create(cliente=self.c, nome="Doces", quantidade_vendida=10, faturamento=30000)
        Categoria.objects.create(cliente=self.c, nome="Bebidas", quantidade_vendida=0, faturamento=10000)
        Categoria.objects.create(cliente=self.c, nome="Sem vendas", faturamento=0)
        ficha = criar_ficha(self.c, "Bolo", [(self.farinha, "kg", "1")])  # custo R$ 10,00
        nova_precificacao(self.c, ficha)  # markup 50 % -> R$ 15,00
        nova_precificacao(self.c, ficha, percentual_lucro=D("100"))  # R$ 20,00

    def ind(self, usuario=None, unidade=None, inicio=None, fim=None):
        return indicadores(usuario or self.user, unidade or self.c, inicio or dia(-7), fim or dia(7), HOJE)

    def test_resultado_do_periodo_por_caixa(self):
        r = self.ind()["resultado"]
        self.assertEqual((r["receitas"], r["despesas"], r["resultado"], r["negativo"]), ("R$ 600,00", "R$ 100,00", "R$ 500,00", False))
        vazio = self.ind(inicio=dia(-60), fim=dia(-30))["resultado"]  # período sem movimento
        self.assertEqual((vazio["receitas"], vazio["resultado"]), ("R$ 0,00", "R$ 0,00"))

    def test_contas_em_aberto_atrasadas_e_do_periodo(self):
        c = self.ind()["contas"]
        self.assertEqual((c["receber"]["aberto_qtd"], c["receber"]["aberto"], c["receber"]["atrasadas_qtd"], c["receber"]["atrasadas"]), (1, "R$ 400,00", 1, "R$ 400,00"))
        self.assertEqual((c["pagar"]["aberto"], c["pagar"]["atrasadas_qtd"], c["pagar"]["periodo_qtd"], c["pagar"]["periodo"]), ("R$ 200,00", 0, 1, "R$ 200,00"))
        fora = self.ind(inicio=dia(20), fim=dia(30))["contas"]["pagar"]
        self.assertEqual((fora["periodo_qtd"], fora["periodo"], fora["aberto"]), (0, "R$ 0,00", "R$ 200,00"))  # só o período muda

    def test_estoque(self):
        e = self.ind()["estoque"]
        self.assertEqual((e["valor"], e["baixos"], e["vencendo"]), ("R$ 30,00", 1, 1))

    def test_cadastros_precificacao_e_categorias(self):
        ind = self.ind()
        self.assertEqual(ind["cadastros"], {"insumos": 1, "produtos": 1, "precificacoes": 2})
        self.assertEqual(ind["precificacao"]["preco_medio"], "R$ 17,50")  # (15 + 20) / 2
        self.assertEqual(ind["precificacao"]["cmv_medio"], "58,34%")  # média de 66,67 % e 50,00 % (58,335 → HALF_UP)
        nomes = [c["nome"] for c in ind["categorias"]]
        self.assertEqual(nomes, ["Doces", "Bebidas"])  # só com faturamento, do maior para o menor
        self.assertEqual((ind["categorias"][0]["percentual"], ind["categorias"][0]["ticket"]), (D("75.00"), "R$ 30,00"))
        self.assertEqual(ind["categorias"][1]["ticket"], "R$ 0,00")  # sem vendas: ticket neutro

    def test_sem_nada_cadastrado_valores_neutros(self):
        vazio = cliente("Vazia", "34028316000103")
        u = usuario("v", vazio)
        ind = indicadores(u, vazio, dia(-7), dia(7), HOJE)
        self.assertEqual(ind["resultado"]["resultado"], "R$ 0,00")
        self.assertEqual((ind["contas"]["pagar"]["aberto_qtd"], ind["estoque"]["valor"], ind["cadastros"]["insumos"]), (0, "R$ 0,00", 0))
        self.assertFalse(ind["precificacao"]["tem_dados"])
        self.assertEqual(ind["categorias"], [])

    def test_so_aparece_o_que_o_usuario_pode_ver(self):
        so_estoque = usuario("e", self.c, permissoes=[("estoque", "view_movimentoestoque")])
        self.assertEqual(set(self.ind(so_estoque)), {"estoque"})
        sem = usuario("s", self.c, permissoes=[("auth", "view_user")])
        self.assertEqual(self.ind(sem), {})
        so_pagar = usuario("p", self.c, permissoes=[("financeiro", "view_contapagar")])
        self.assertEqual(set(self.ind(so_pagar)["contas"]), {"pagar"})  # sem permissão de receber

    def test_modulo_desabilitado_some_mesmo_com_permissao(self):
        enxuta = cliente("Enxuta", "45723174000110", modulos=("insumo",))
        u = usuario("x", enxuta)
        self.assertEqual(set(indicadores(u, enxuta, dia(-7), dia(7), HOJE)), {"cadastros"})
        self.assertEqual(set(indicadores(u, enxuta, dia(-7), dia(7), HOJE)["cadastros"]), {"insumos"})

    def test_nunca_mistura_unidades(self):
        outra = cliente("Outra", "11444777000161", matriz=self.c)
        cat = CategoriaFinanceira.objects.create(cliente=outra, nome="Vendas", tipo="receita")
        c2 = ContaReceber.objects.create(cliente=outra, descricao="Da filial", categoria=cat, valor=999900, emissao=dia(-5), vencimento=dia(-1))
        registrar_baixa(self.user, c2, 999900, dia(0))
        self.assertEqual(self.ind()["resultado"]["receitas"], "R$ 600,00")  # a matriz não soma a filial
        self.assertEqual(self.ind(unidade=outra)["resultado"]["receitas"], "R$ 9.999,00")


class PainelTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", matriz=self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)

    def test_login_obrigatorio_e_pagina_inicial(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Painel")
        self.assertEqual(resp.context["unidade_atual"], self.matriz)

    def test_seletor_de_unidade_e_escopo(self):
        self.assertFalse(self.client.get(reverse("index")).context["mostrar_unidade"])
        resp = self.client.get(reverse("index"), {"unidade": self.filial.pk})
        self.assertEqual(resp.context["unidade_atual"], self.matriz)  # sem permissão de filiais: ignora
        self.client.force_login(usuario("m", self.matriz, filiais=("view",)))
        resp = self.client.get(reverse("index"), {"unidade": self.filial.pk})
        self.assertEqual((resp.context["unidade_atual"], resp.context["mostrar_unidade"]), (self.filial, True))
        resp = self.client.get(reverse("index"), {"unidade": self.outra.pk})
        self.assertEqual(resp.context["unidade_atual"], self.matriz)  # outra empresa: ignora

    def test_periodo_pela_querystring(self):
        ctx = self.client.get(reverse("index"), {"periodo": "personalizado", "inicio": "2026-08-01", "fim": "2026-08-15"}).context
        self.assertEqual((ctx["preset"], ctx["inicio"], ctx["fim"]), ("personalizado", date(2026, 8, 1), date(2026, 8, 15)))
        ctx = self.client.get(reverse("index"), {"periodo": "personalizado", "inicio": "lixo", "fim": "2026-08-15"}).context
        self.assertEqual(ctx["preset"], "mes_atual")  # período inválido não quebra a tela

    def test_usuario_sem_permissao_ve_aviso_e_sem_cliente_nao_quebra(self):
        self.client.force_login(usuario("s", self.matriz, permissoes=[]))
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "ainda não tem permissão")
        self.assertNotContains(resp, 'id="btnOnline"')
        sem_cliente = User.objects.create_user(username="sc", email="sc@t.local", password="SenhaForte123!")
        self.client.force_login(sem_cliente)
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "não está vinculado")

    def test_sem_permissao_do_painel_abre_a_primeira_tela_do_menu(self):
        from web_project.menu import montar_menu
        from web_project.testing import habilitar_modulo

        habilitar_modulo(self.matriz, "insumo")
        u = usuario("sp", self.matriz, permissoes=[("insumo", "view_insumo")])
        self.client.force_login(u)
        esperado = next(i["url"] for i in montar_menu(u) if i["slug"] != "painel")
        resp = self.client.get(reverse("index"))
        self.assertRedirects(resp, reverse(esperado), fetch_redirect_response=False)

    def test_menu_tem_o_painel(self):
        self.assertContains(self.client.get(reverse("index")), 'href="/"')


class PresencaTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", matriz=self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)

    def test_middleware_grava_uma_vez_por_minuto_por_sessao(self):
        self.client.force_login(self.user)
        self.assertFalse(Presenca.objects.exists())
        with mock.patch("apps.dashboard.middleware.registrar_atividade", wraps=registrar_atividade) as espiao:
            self.client.get(reverse("index"))
            self.client.get(reverse("index"))
            self.client.get(reverse("insumo"))
            self.assertEqual(espiao.call_count, 1)
            sessao = self.client.session
            sessao["presenca_gravada_em"] = 0  # passou mais de um minuto
            sessao.save()
            self.client.get(reverse("index"))
            self.assertEqual(espiao.call_count, 2)
        self.assertEqual(Presenca.objects.count(), 1)  # uma linha por usuário

    def test_anonimo_nao_gera_presenca_e_falha_nao_derruba_a_requisicao(self):
        self.client.get(reverse("login"))
        self.assertFalse(Presenca.objects.exists())
        self.client.force_login(self.user)
        with mock.patch("apps.dashboard.middleware.registrar_atividade", side_effect=RuntimeError("banco indisponível")):
            self.assertEqual(self.client.get(reverse("index")).status_code, 200)

    def test_online_so_ate_5_minutos_e_usuario_ativo(self):
        agora = timezone.now()
        recente = usuario("recente", self.matriz)
        antigo = usuario("antigo", self.matriz)
        inativo = usuario("inativo", self.matriz)
        registrar_atividade(recente, agora - timedelta(minutes=MINUTOS_ONLINE - 1))
        registrar_atividade(antigo, agora - timedelta(minutes=MINUTOS_ONLINE + 1))
        registrar_atividade(inativo, agora)
        User.objects.filter(pk=inativo.pk).update(is_active=False)
        total, lista = usuarios_online(self.user, agora)
        self.assertEqual((total, [u["nome"] for u in lista]), (1, ["recente"]))
        self.assertEqual(lista[0]["minutos"], MINUTOS_ONLINE - 1)

    def test_online_respeita_o_escopo_do_solicitante(self):
        agora = timezone.now()
        da_filial, de_outra = usuario("filial", self.filial), usuario("outra", self.outra)
        super_usuario = usuario("root", self.matriz, is_superuser=True)
        for u in (self.user, da_filial, de_outra, super_usuario):
            registrar_atividade(u, agora)
        nomes = lambda quem: sorted(u["nome"] for u in usuarios_online(quem, agora)[1])
        self.assertEqual(nomes(self.user), ["filial", "u"])  # mesma estrutura; sem outra empresa nem superusuário
        self.assertEqual(nomes(da_filial), ["filial", "u"])
        self.assertEqual(nomes(super_usuario), ["filial", "outra", "root", "u"])  # superusuário vê todos

    def test_dados_expostos_sao_minimos(self):
        agora = timezone.now()
        registrar_atividade(self.user, agora)
        _, lista = usuarios_online(self.user, agora)
        self.assertEqual(set(lista[0]), {"nome", "unidade", "minutos"})  # sem e-mail, IP, id...
        self.assertEqual(lista[0]["unidade"], "Matriz")

    def test_endpoint_exige_login_e_permissao_e_nao_vaza_dados(self):
        self.assertEqual(self.client.get(reverse("dashboard-online")).status_code, 302)
        sem = usuario("s", self.matriz, permissoes=[])
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("dashboard-online")).status_code, 403)
        self.client.force_login(self.user)
        registrar_atividade(usuario("filial", self.filial), timezone.now())
        registrar_atividade(usuario("outra", self.outra), timezone.now())
        corpo = self.client.get(reverse("dashboard-online")).content.decode()
        dados = json.loads(corpo)
        self.assertEqual(set(dados), {"total", "limite_minutos", "usuarios"})
        self.assertNotIn("@t.local", corpo)
        self.assertNotIn("outra", [u["nome"] for u in dados["usuarios"]])
        self.assertIn("filial", [u["nome"] for u in dados["usuarios"]])
        self.assertEqual(self.client.post(reverse("dashboard-online")).status_code, 405)

    def test_contador_do_painel_so_para_quem_pode_ver(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("index")), 'id="btnOnline"')
        self.client.force_login(usuario("s", self.matriz, permissoes=[("insumo", "view_insumo"), ("dashboard", "view_painel")]))
        self.assertNotContains(self.client.get(reverse("index")), 'id="btnOnline"')

    def test_logout_remove_da_lista_na_hora(self):
        self.client.force_login(self.user)
        self.client.get(reverse("index"))
        self.assertTrue(Presenca.objects.filter(usuario=self.user).exists())
        self.client.post(reverse("logout"))
        self.assertFalse(Presenca.objects.filter(usuario=self.user).exists())
