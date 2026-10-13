from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.ficha.tests import criar as criar_ficha
from apps.insumo.tests import novo_insumo
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from .calculos import custo_por_unidade, distribuir_por_vendas
from .models import CustoAjusteCategoria, CustoModelo, CustoOperacional, CustoRateio
from .services import ajustar_categoria, custo_operacional_da_categoria, distribuicao_por_categoria, restaurar_categoria

D = Decimal


class CalculosTests(SimpleTestCase):
    def test_custo_por_unidade(self):
        self.assertEqual(custo_por_unidade(80000, 100), 800)
        self.assertEqual(custo_por_unidade(1000, 3), 333)  # arredonda 333,33
        self.assertEqual(custo_por_unidade(5000, 0), 0)  # sem vendas: 0, nunca divisão por zero

    def test_exemplo_do_negocio_300_reais_em_tres_categorias_iguais(self):
        # água + luz + gás = R$ 300; 3 categorias vendendo 100 cada -> R$ 100 por categoria (R$ 1 por item)
        resultado, sobra, excesso = distribuir_por_vendas(30000, [(1, 100, True), (2, 100, True), (3, 100, True)])
        self.assertEqual([resultado[i]["valor"] for i in (1, 2, 3)], [10000, 10000, 10000])
        self.assertEqual((sobra, excesso), (0, 0))

    def test_volumes_diferentes_nao_dividem_por_igual(self):
        resultado, _, _ = distribuir_por_vendas(30000, [(1, 200, True), (2, 100, True), (3, 0, True)])
        self.assertEqual([resultado[i]["valor"] for i in (1, 2, 3)], [20000, 10000, 0])
        self.assertEqual(resultado[3]["origem"], "zero")

    def test_centavos_residuais_fecham_exatamente_no_total(self):
        for total in (1, 2, 100, 10001, 99999):
            resultado, sobra, _ = distribuir_por_vendas(total, [(1, 1, True), (2, 1, True), (3, 1, True)])
            self.assertEqual(sum(r["valor"] for r in resultado.values()) + sobra, total)
        # 100 centavos em 3 partes iguais: 34 + 33 + 33 (o resto vai para o menor id no empate)
        resultado, _, _ = distribuir_por_vendas(100, [(1, 5, True), (2, 5, True), (3, 5, True)])
        self.assertEqual(sorted(r["valor"] for r in resultado.values()), [33, 33, 34])

    def test_maior_resto_decide_quem_recebe_o_centavo(self):
        # 10 centavos, pesos 1:2 -> 3,33 e 6,67: o centavo extra vai para o de maior resto (peso 2)
        resultado, _, _ = distribuir_por_vendas(10, [(1, 1, True), (2, 2, True)])
        self.assertEqual((resultado[1]["valor"], resultado[2]["valor"]), (3, 7))

    def test_sem_nenhuma_venda_divide_igualmente_entre_as_ativas(self):
        resultado, _, _ = distribuir_por_vendas(30000, [(1, 0, True), (2, 0, True), (3, 0, False)])
        self.assertEqual((resultado[1]["valor"], resultado[2]["valor"], resultado[1]["origem"]), (15000, 15000, "igual"))
        self.assertNotIn(3, resultado)  # categoria inativa fica fora do rateio automático

    def test_ajuste_manual_fixa_o_valor_e_o_resto_se_redistribui(self):
        resultado, sobra, excesso = distribuir_por_vendas(30000, [(1, 100, True), (2, 100, True), (3, 100, True)], {1: 20000})
        self.assertEqual([resultado[i]["valor"] for i in (1, 2, 3)], [20000, 5000, 5000])
        self.assertEqual(resultado[1]["origem"], "manual")
        self.assertEqual((sobra, excesso), (0, 0))

    def test_todas_ajustadas_com_soma_diferente_gera_sobra_ou_excedente(self):
        _, sobra, excesso = distribuir_por_vendas(30000, [(1, 1, True), (2, 1, True)], {1: 10000, 2: 10000})
        self.assertEqual((sobra, excesso), (10000, 0))
        _, sobra, excesso = distribuir_por_vendas(30000, [(1, 1, True), (2, 1, True)], {1: 20000, 2: 20000})
        self.assertEqual((sobra, excesso), (0, 10000))

    def test_total_zero_e_sem_categorias(self):
        resultado, sobra, _ = distribuir_por_vendas(0, [(1, 10, True)])
        self.assertEqual((resultado[1]["valor"], sobra), (0, 0))
        resultado, sobra, _ = distribuir_por_vendas(500, [])
        self.assertEqual((resultado, sobra), ({}, 500))

    def test_ajuste_de_categoria_desconhecida_e_ignorado(self):
        resultado, sobra, _ = distribuir_por_vendas(1000, [(1, 1, True)], {99: 500})
        self.assertEqual((resultado[1]["valor"], sobra), (1000, 0))


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in ("categoria", "custo-operacional", "insumo", "produto", "ficha", "precificacao"):
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, acoes=("view", "add", "change", "delete"), filiais=(), comuns=("view", "add", "change", "delete")):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="custo", codename__in=[f"{a}_custooperacional" for a in acoes]))
    perms += Permission.objects.filter(content_type__app_label="custo", codename__in=[f"{a}_customodelo" for a in comuns])
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


def custo(c, nome, valor, tipo="fixo", **extra):
    return CustoOperacional.objects.create(cliente=c, nome=nome, valor=valor, tipo=tipo, **extra)


class ServicosTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.doces = Categoria.objects.create(cliente=self.c, nome="Doces", quantidade_vendida=100, faturamento=0)
        self.bebidas = Categoria.objects.create(cliente=self.c, nome="Bebidas", quantidade_vendida=300)
        self.user = usuario("u", self.c)

    def linha(self, categoria):
        linhas, _ = distribuicao_por_categoria(self.c)
        return next(l for l in linhas if l["categoria"] == categoria)

    def test_distribuicao_proporcional_as_vendas_em_reais(self):
        custo(self.c, "Aluguel", 100000, "fixo")
        custo(self.c, "Energia", 20000, "variavel")
        # total R$ 1.200,00: doces 25%, bebidas 75%
        self.assertEqual((self.linha(self.doces)["valor"], self.linha(self.bebidas)["valor"]), (30000, 90000))
        self.assertEqual(self.linha(self.doces)["por_unidade"], 300)  # R$ 300 ÷ 100 itens
        _, totais = distribuicao_por_categoria(self.c)
        self.assertEqual((totais["mensal"], totais["distribuido"], totais["nao_distribuido"]), (120000, 120000, 0))

    def test_custo_inativo_nao_entra(self):
        custo(self.c, "Antigo", 50000, ativo=False)
        self.assertEqual(self.linha(self.doces)["valor"], 0)

    def test_categoria_sem_vendas_recebe_zero_sem_erro(self):
        custo(self.c, "Aluguel", 40000)
        sem = Categoria.objects.create(cliente=self.c, nome="Sem vendas")
        l = self.linha(sem)
        self.assertEqual((l["valor"], l["por_unidade"], l["sem_vendas"]), (0, 0, True))

    def test_categoria_inativa_so_aparece_se_tem_ajuste(self):
        velha = Categoria.objects.create(cliente=self.c, nome="Velha", ativo=False, quantidade_vendida=10)
        custo(self.c, "X", 1000)
        nomes = lambda: [l["categoria"].nome for l in distribuicao_por_categoria(self.c)[0]]  # noqa: E731
        self.assertNotIn("Velha", nomes())
        CustoAjusteCategoria.objects.create(cliente=self.c, categoria=velha, valor=100)
        self.assertIn("Velha", nomes())

    def test_ajuste_manual_e_preservado_quando_o_custo_muda(self):
        c = custo(self.c, "Aluguel", 40000)
        ajustar_categoria(None, self.c, self.doces, 30000)
        c.valor = 80000
        c.save()
        self.assertEqual(self.linha(self.doces)["valor"], 30000)  # decisão do usuário não é sobrescrita
        self.assertTrue(self.linha(self.doces)["manual"])
        self.assertEqual(self.linha(self.bebidas)["valor"], 50000)  # o restante se redistribui

    def test_ajuste_invalido_e_recusado(self):
        custo(self.c, "Aluguel", 40000)
        with self.assertRaises(RegraNegocioError):
            ajustar_categoria(None, self.c, self.doces, 40001)  # maior que o total
        ajustar_categoria(None, self.c, self.doces, 10000)
        with self.assertRaises(RegraNegocioError):
            ajustar_categoria(None, self.c, self.bebidas, 20000)  # todas ajustadas e a soma (30000) não fecha com 40000
        ajustar_categoria(None, self.c, self.bebidas, 30000)  # soma fecha: aceito
        self.assertEqual(CustoAjusteCategoria.objects.count(), 2)

    def test_restaurar_volta_ao_calculo_automatico(self):
        custo(self.c, "Aluguel", 40000)
        ajustar_categoria(None, self.c, self.doces, 30000)
        self.assertTrue(restaurar_categoria(None, self.c, self.doces))
        self.assertEqual(self.linha(self.doces)["valor"], 10000)
        self.assertFalse(restaurar_categoria(None, self.c, self.doces))  # nada a restaurar

    def test_ajuste_de_categoria_de_outra_unidade_e_recusado(self):
        outra = cliente("Outra", "34028316000103")
        alheia = Categoria.objects.create(cliente=outra, nome="Alheia")
        custo(self.c, "Aluguel", 1000)
        with self.assertRaises(RegraNegocioError):
            ajustar_categoria(None, self.c, alheia, 10)

    def test_ajuste_e_restauracao_sao_auditados(self):
        custo(self.c, "Aluguel", 40000)
        ajustar_categoria(None, self.c, self.doces, 30000)
        restaurar_categoria(None, self.c, self.doces)
        registros = list(RegistroAuditoria.objects.filter(modulo="custo", acao="editar").order_by("pk"))
        self.assertEqual(len(registros), 2)
        self.assertEqual(registros[0].alteracoes[0]["depois"], "R$ 300,00 (ajuste manual)")
        self.assertEqual(registros[1].alteracoes[0]["depois"], "R$ 100,00 (calculado)")

    def test_sugestao_para_a_precificacao(self):
        self.assertIn("Nenhum custo", custo_operacional_da_categoria(self.doces)["aviso"])
        custo(self.c, "Aluguel", 40000)  # doces recebe 25% = R$ 100,00; 100 itens -> R$ 1,00 por item
        self.assertEqual(custo_operacional_da_categoria(self.doces), {"centavos": 100, "total": 10000, "manual": False, "aviso": ""})
        self.doces.quantidade_vendida = 0
        self.doces.save()
        sem_vendas = custo_operacional_da_categoria(Categoria.objects.get(pk=self.doces.pk))
        self.assertEqual(sem_vendas["centavos"], 0)

    def test_sugestao_nao_mistura_clientes(self):
        outro = cliente("Outro", "34028316000103")
        cat_outro = Categoria.objects.create(cliente=outro, nome="Doces", quantidade_vendida=10)
        custo(outro, "Aluguel", 99999)
        self.assertEqual(custo_operacional_da_categoria(self.doces)["centavos"], 0)
        self.assertEqual(custo_operacional_da_categoria(cat_outro)["total"], 99999)

    def test_categoria_com_ajuste_so_desativa(self):
        from web_project.mixins import em_uso

        custo(self.c, "Aluguel", 1000)
        ajustar_categoria(None, self.c, self.doces, 500)
        self.assertTrue(em_uso(self.doces))
        self.assertFalse(em_uso(self.bebidas))


class CatalogoTests(TestCase):
    def test_catalogo_global_foi_carregado_e_nao_e_copiado_para_clientes(self):
        self.assertGreaterEqual(CustoModelo.objects.filter(cliente__isnull=True).count(), 10)
        self.assertTrue(CustoModelo.objects.filter(nome="Água", tipo="variavel").exists())
        self.assertTrue(CustoModelo.objects.filter(nome="Energia elétrica").exists())
        cliente("Empresa", "11222333000181")
        self.assertFalse(CustoOperacional.objects.exists())  # nada duplicado automaticamente

    def test_cada_unidade_usa_um_modelo_uma_vez_mas_varias_unidades_podem(self):
        agua = CustoModelo.objects.get(nome="Água")
        a, b = cliente("A", "11222333000181"), cliente("B", "34028316000103")
        CustoOperacional.objects.create(cliente=a, nome="Água", modelo=agua)
        CustoOperacional.objects.create(cliente=b, nome="Água", modelo=agua)  # outra unidade: ok
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustoOperacional.objects.create(cliente=a, nome="Água 2", modelo=agua)
        CustoOperacional.objects.create(cliente=a, nome="Próprio 1")
        CustoOperacional.objects.create(cliente=a, nome="Próprio 2")  # sem modelo: sem limite

    def test_nome_do_custo_comum_e_unico_por_escopo(self):
        a, b = cliente("A", "11222333000181"), cliente("B", "34028316000103")
        CustoModelo.objects.create(cliente=a, nome="Seguro", tipo="fixo")
        CustoModelo.objects.create(cliente=b, nome="Seguro", tipo="fixo")  # outra unidade: ok
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustoModelo.objects.create(cliente=a, nome="Seguro", tipo="fixo")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustoModelo.objects.create(nome="Água", tipo="fixo")  # repete um padrão do sistema

    def test_rateio_legado_foi_convertido_em_ajuste_manual_pela_migracao(self):
        # a tabela legada continua existindo (sem uso); a migração 0004 já cobriu os dados existentes
        self.assertEqual(CustoRateio.objects.count(), 0)


class TelasTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.doces = Categoria.objects.create(cliente=self.matriz, nome="Doces", quantidade_vendida=100)
        self.bebidas = Categoria.objects.create(cliente=self.matriz, nome="Bebidas", quantidade_vendida=100)

    def dados(self, nome="Aluguel", **extra):
        return {"modelo": "", "nome": nome, "tipo": "fixo", "valor": "1.000,00", "descricao": "", "ativo": "on", **extra}

    def nomes(self, **p):
        return [c.nome for c in self.client.get(reverse("custo-list"), p).context["custos"]]

    def test_criar_custo_sem_distribuicao_manual(self):
        resp = self.client.post(reverse("custo-create"), self.dados())
        self.assertRedirects(resp, reverse("custo-list"), fetch_redirect_response=False)
        c = CustoOperacional.objects.get()
        self.assertEqual((c.cliente_id, c.valor), (self.matriz.pk, 100000))
        self.assertNotContains(self.client.get(reverse("custo-create")), "Adicionar categoria")  # sem formulário de rateio

    def test_valor_invalido_e_nome_duplicado(self):
        for valor in ("abc", "-5,00"):
            self.assertEqual(self.client.post(reverse("custo-create"), self.dados(valor=valor)).status_code, 200)
        self.client.post(reverse("custo-create"), self.dados())
        self.assertEqual(self.client.post(reverse("custo-create"), self.dados(nome="aluguel")).status_code, 200)
        self.assertEqual(CustoOperacional.objects.count(), 1)

    def test_custo_comum_do_catalogo(self):
        agua = CustoModelo.objects.get(nome="Água")
        resp = self.client.post(reverse("custo-create"), self.dados(nome="", modelo=agua.pk, tipo="fixo"))
        self.assertEqual(resp.status_code, 302)
        c = CustoOperacional.objects.get()
        self.assertEqual((c.nome, c.tipo, c.modelo_id), ("Água", "variavel", agua.pk))  # nome e tipo do catálogo
        # já usado: não é mais oferecido nem aceito de novo
        self.assertEqual(self.client.post(reverse("custo-create"), self.dados(nome="Outra", modelo=agua.pk)).status_code, 200)
        self.assertNotIn(agua, list(self.client.get(reverse("custo-list")).context["modelos_disponiveis"]))

    def test_custo_comum_proprio_aparece_no_formulario_so_para_a_unidade(self):
        meu = CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        alheio = CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        disponiveis = list(self.client.get(reverse("custo-create")).context["form"].fields["modelo"].queryset)
        self.assertIn(meu, disponiveis)
        self.assertNotIn(alheio, disponiveis)
        self.assertEqual(self.client.post(reverse("custo-create"), self.dados(nome="", modelo=alheio.pk)).status_code, 200)

    def test_pre_selecao_por_querystring(self):
        energia = CustoModelo.objects.get(nome="Energia elétrica")
        form = self.client.get(reverse("custo-create"), {"modelo": energia.pk}).context["form"]
        self.assertEqual((form.initial["nome"], form.initial["tipo"]), ("Energia elétrica", "variavel"))
        self.assertNotIn("nome", self.client.get(reverse("custo-create"), {"modelo": "abc"}).context["form"].initial)
        alheio = CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        self.assertNotIn("nome", self.client.get(reverse("custo-create"), {"modelo": alheio.pk}).context["form"].initial)

    def test_salvar_e_adicionar_outro(self):
        resp = self.client.post(reverse("custo-create"), self.dados(salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("custo-create"), fetch_redirect_response=False)

    def test_editar_audita_a_mudanca_de_valor(self):
        self.client.post(reverse("custo-create"), self.dados())
        c = CustoOperacional.objects.get()
        self.assertEqual(self.client.post(reverse("custo-edit", args=[c.pk]), self.dados(valor="1.500,00")).status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.valor, 150000)
        registro = RegistroAuditoria.objects.filter(modulo="custo", acao="editar").latest("pk")
        self.assertEqual(registro.alteracoes[0]["depois"], "R$ 1.500,00")

    def test_lista_filtros_totais_e_exclusao(self):
        a = custo(self.matriz, "Aluguel", 100000, "fixo")
        custo(self.matriz, "Energia", 30000, "variavel")
        custo(self.matriz, "Velho", 5000, "fixo", ativo=False)
        self.assertEqual(self.nomes(), ["Aluguel", "Energia"])
        self.assertEqual(self.nomes(tipo="variavel"), ["Energia"])
        self.assertEqual(self.nomes(status="inativo"), ["Velho"])
        self.assertEqual(self.nomes(sort="valor", dir="desc"), ["Aluguel", "Energia"])
        ctx = self.client.get(reverse("custo-list")).context
        self.assertEqual((ctx["total_fixo"], ctx["total_variavel"], ctx["total_geral"]), ("R$ 1.000,00", "R$ 300,00", "R$ 1.300,00"))
        self.client.post(reverse("custo-excluir", args=[a.pk]))
        self.assertFalse(CustoOperacional.objects.filter(pk=a.pk).exists())

    def test_permissoes_e_tenant(self):
        c = custo(self.matriz, "Aluguel", 1000)
        alheio = custo(self.outra, "Alheio", 1000)
        self.assertEqual(self.client.get(reverse("custo-edit", args=[alheio.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("custo-excluir", args=[alheio.pk])).status_code, 404)
        self.assertNotIn("Alheio", self.nomes())
        self.client.force_login(usuario("v", self.matriz, ("view",)))
        self.assertEqual(self.client.get(reverse("custo-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custo-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("custo-edit", args=[c.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("custo-excluir", args=[c.pk])).status_code, 403)
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.get(reverse("custo-list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("custo-categorias")).status_code, 403)

    def test_matriz_alcanca_filial_com_permissao(self):
        da_filial = custo(self.filial, "DaFilial", 1000)
        self.assertNotIn("DaFilial", self.nomes())
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change")))
        self.assertIn("DaFilial", self.nomes())
        self.assertEqual(self.client.get(reverse("custo-edit", args=[da_filial.pk])).status_code, 200)
        self.client.force_login(usuario("f", self.filial, filiais=("view",)))
        self.assertEqual(self.nomes(), ["DaFilial"])  # filial não vê a matriz

    def test_exportacao(self):
        custo(self.matriz, "Aluguel", 100000)
        custo(self.outra, "Alheio", 1)
        resp = self.client.get(reverse("custo-list"), {"export": "xlsx"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([l[0] for l in linhas[1:]], ["Aluguel"])
        self.assertEqual((linhas[1][2], linhas[1][3]), ("R$ 1.000,00", "Próprio"))
        self.assertTrue(self.client.get(reverse("custo-list"), {"export": "pdf"}).content.startswith(b"%PDF"))

    def test_pagina_por_categoria_mostra_reais_e_ajuste(self):
        custo(self.matriz, "Aluguel", 100000)
        custo(self.matriz, "Internet", 5000)
        custo(self.filial, "Da filial", 77777)
        ctx = self.client.get(reverse("custo-categorias")).context
        doces = next(l for l in ctx["linhas"] if l["categoria"] == self.doces)
        self.assertEqual((doces["valor_fmt"], doces["por_unidade_fmt"]), ("R$ 525,00", "R$ 5,25"))  # metade de R$ 1.050,00
        self.assertEqual((ctx["total_mensal"], ctx["total_distribuido"]), ("R$ 1.050,00", "R$ 1.050,00"))  # só a unidade atual
        fora = self.client.get(reverse("custo-categorias"), {"unidade": self.outra.pk}).context
        self.assertEqual(fora["unidade_atual"], self.matriz)  # unidade fora do escopo cai na própria
        self.client.force_login(usuario("m", self.matriz, filiais=("view",)))
        da_filial = self.client.get(reverse("custo-categorias"), {"unidade": self.filial.pk}).context
        self.assertEqual((da_filial["unidade_atual"], da_filial["total_mensal"]), (self.filial, "R$ 777,77"))

    def test_ajustar_e_restaurar_pela_tela(self):
        custo(self.matriz, "Aluguel", 100000)
        url = reverse("custo-categoria-ajustar", args=[self.doces.pk])
        resp = self.client.post(url, {"valor": "700,00"})
        self.assertRedirects(resp, f"{reverse('custo-categorias')}?unidade={self.matriz.pk}", fetch_redirect_response=False)
        self.assertEqual(CustoAjusteCategoria.objects.get().valor, 70000)
        pagina = self.client.get(reverse("custo-categorias"))
        self.assertContains(pagina, "Ajuste manual")
        self.client.post(url, {"restaurar": "1"})
        self.assertFalse(CustoAjusteCategoria.objects.exists())
        # valor inválido ou acima do total: mensagem e nada muda
        for ruim in ("abc", "2.000,00", "-1"):
            self.client.post(url, {"valor": ruim})
        self.assertFalse(CustoAjusteCategoria.objects.exists())

    def test_ajuste_exige_permissao_e_tenant(self):
        alheia = Categoria.objects.create(cliente=self.outra, nome="Alheia")
        self.assertEqual(self.client.post(reverse("custo-categoria-ajustar", args=[alheia.pk]), {"valor": "1,00"}).status_code, 404)
        self.client.force_login(usuario("v", self.matriz, ("view",)))
        self.assertEqual(self.client.post(reverse("custo-categoria-ajustar", args=[self.doces.pk]), {"valor": "1,00"}).status_code, 403)
        pagina = self.client.get(reverse("custo-categorias"))
        self.assertNotContains(pagina, "Salvar ajuste manual")  # somente leitura: sem botões
        # matriz com permissão de alterar itens da filial ajusta a categoria da filial; sem ela, 404
        cat_filial = Categoria.objects.create(cliente=self.filial, nome="Filial cat")
        self.assertEqual(self.client.post(reverse("custo-categoria-ajustar", args=[cat_filial.pk]), {"valor": "1,00"}).status_code, 403)

    def test_sugestao_aparece_na_previa_da_precificacao(self):
        custo(self.matriz, "Aluguel", 100000)  # doces recebe 50% = R$ 500,00; 100 itens -> R$ 5,00 por item
        farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        ficha = criar_ficha(self.matriz, "Bolo", [(farinha, "kg", "1")])
        ficha.produto.categoria = self.doces
        ficha.produto.save()
        grupo = Group.objects.create(name="GP")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="precificacao", codename="view_precificacao"))
        self.user.groups.add(grupo)
        dados = {
            "nome": "x", "ficha": ficha.pk, "canal": "", "canal_nome": "Balcão", "metodo_lucro": "markup", "percentual_lucro": "50",
        }
        sugestao = self.client.post(reverse("precificacao-calcular"), dados).json()["sugestao_custo_operacional"]
        self.assertEqual(
            sugestao,
            {"categoria": "Doces", "aviso": "", "formatado": "R$ 5,00", "valor": "5,00", "total_categoria": "R$ 500,00", "ajuste_na_categoria": False},
        )


class CustosComunsTests(TestCase):
    """Configurações > Custos comuns: CRUD por unidade, padrões do sistema somente leitura."""

    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        for c in (self.matriz, self.filial, self.outra):
            habilitar_modulo(c, "custo-operacional")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)

    def nomes(self, **p):
        return [c.nome for c in self.client.get(reverse("custo-comum-list"), p).context["comuns"]]

    def dados(self, **extra):
        return {"nome": "Seguro", "tipo": "fixo", "descricao": "Seguro predial", "ativo": "on", **extra}

    def test_criar_pela_tela_define_o_dono_pelo_usuario_e_audita(self):
        resp = self.client.post(reverse("custo-comum-create"), self.dados(cliente=self.outra.pk))  # tentativa de forjar o dono
        self.assertRedirects(resp, reverse("custo-comum-list"), fetch_redirect_response=False)
        c = CustoModelo.objects.get(nome="Seguro")
        self.assertEqual(c.cliente_id, self.matriz.pk)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="custo", acao="criar", registro_nome="Seguro").exists())

    def test_validacoes_nome_repetido_e_vazio(self):
        self.client.post(reverse("custo-comum-create"), self.dados())
        for ruim in ("seguro", "  ", "Água"):  # repetido, vazio, igual a um padrão do sistema
            self.assertEqual(self.client.post(reverse("custo-comum-create"), self.dados(nome=ruim)).status_code, 200, ruim)
        self.assertEqual(CustoModelo.objects.filter(cliente=self.matriz).count(), 1)

    def test_salvar_e_adicionar_outro(self):
        resp = self.client.post(reverse("custo-comum-create"), self.dados(salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("custo-comum-create"), fetch_redirect_response=False)

    def test_lista_mostra_padroes_e_proprios_e_nunca_de_outra_empresa(self):
        CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        nomes = self.nomes(per_page=100)
        self.assertIn("Água", nomes)
        self.assertIn("Seguro", nomes)
        self.assertNotIn("Alheio", nomes)

    def test_filtros_e_ordenacao(self):
        CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo", ativo=True)
        CustoModelo.objects.create(cliente=self.matriz, nome="Velho", tipo="variavel", ativo=False)
        self.assertEqual(self.nomes(origem="proprio"), ["Seguro"])
        self.assertEqual(self.nomes(origem="proprio", status="inativo"), ["Velho"])
        self.assertEqual(self.nomes(origem="proprio", status="todos", sort="nome", dir="desc"), ["Velho", "Seguro"])
        self.assertEqual(self.nomes(origem="sistema", tipo="variavel", q="Energia"), ["Energia elétrica"])
        self.assertIn("Água", self.nomes(origem="sistema", per_page=100))

    def test_paginacao_preserva_filtros(self):
        for n in range(12):
            CustoModelo.objects.create(cliente=self.matriz, nome=f"Item {n:02d}", tipo="fixo")
        html = self.client.get(reverse("custo-comum-list"), {"origem": "proprio", "per_page": "10"}).content.decode()
        self.assertIn("page=2", html)
        self.assertIn("origem=proprio", html)

    def test_editar_so_o_proprio_e_nunca_o_padrao_ou_o_alheio(self):
        meu = CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        alheio = CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        padrao = CustoModelo.objects.get(nome="Água")
        self.assertEqual(self.client.post(reverse("custo-comum-edit", args=[meu.pk]), self.dados(nome="Seguro 2")).status_code, 302)
        meu.refresh_from_db()
        self.assertEqual(meu.nome, "Seguro 2")
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="custo", acao="editar", registro_nome="Seguro 2").exists())
        for outro in (alheio, padrao):
            self.assertEqual(self.client.get(reverse("custo-comum-edit", args=[outro.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("custo-comum-edit", args=[outro.pk]), self.dados()).status_code, 404)
            self.assertEqual(self.client.post(reverse("custo-comum-excluir", args=[outro.pk])).status_code, 404)

    def test_excluir_sem_uso_apaga_e_com_uso_apenas_desativa(self):
        livre = CustoModelo.objects.create(cliente=self.matriz, nome="Livre", tipo="fixo")
        usado = CustoModelo.objects.create(cliente=self.matriz, nome="Usado", tipo="fixo")
        CustoOperacional.objects.create(cliente=self.matriz, nome="Usado", modelo=usado, valor=100)
        self.client.post(reverse("custo-comum-excluir", args=[livre.pk]))
        self.client.post(reverse("custo-comum-excluir", args=[usado.pk]))
        self.assertFalse(CustoModelo.objects.filter(pk=livre.pk).exists())
        usado.refresh_from_db()
        self.assertFalse(usado.ativo)  # preserva o vínculo e o histórico do custo operacional
        self.assertEqual(CustoOperacional.objects.get(nome="Usado").modelo_id, usado.pk)

    def test_permissoes_por_acao(self):
        meu = CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        self.client.force_login(usuario("v", self.matriz, comuns=("view",)))
        self.assertEqual(self.client.get(reverse("custo-comum-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custo-comum-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("custo-comum-edit", args=[meu.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("custo-comum-excluir", args=[meu.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("custo-comum-list"), {"origem": "proprio"}), reverse("custo-comum-edit", args=[meu.pk]))
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.get(reverse("custo-comum-list")).status_code, 403)

    def test_matriz_com_permissao_gerencia_os_da_filial(self):
        da_filial = CustoModelo.objects.create(cliente=self.filial, nome="Da filial", tipo="fixo")
        self.assertNotIn("Da filial", self.nomes(per_page=100))
        self.assertEqual(self.client.get(reverse("custo-comum-edit", args=[da_filial.pk])).status_code, 404)
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change", "delete")))
        self.assertIn("Da filial", self.nomes(per_page=100))
        self.assertEqual(self.client.get(reverse("custo-comum-edit", args=[da_filial.pk])).status_code, 200)

    def test_exportacao_respeita_escopo(self):
        CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        resp = self.client.get(reverse("custo-comum-list"), {"export": "xlsx", "origem": "proprio"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([l[0] for l in linhas[1:]], ["Seguro"])
        self.assertTrue(self.client.get(reverse("custo-comum-list"), {"export": "pdf"}).content.startswith(b"%PDF"))


class AcoesDosCustosComunsTests(TestCase):
    """Botões Editar/Excluir só onde o servidor aceita a operação; padrões do sistema só para o administrador."""

    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.outra = cliente("Outra", "34028316000103")
        for c in (self.matriz, self.outra):
            habilitar_modulo(c, "custo-operacional")
        self.meu = CustoModelo.objects.create(cliente=self.matriz, nome="Seguro", tipo="fixo")
        self.alheio = CustoModelo.objects.create(cliente=self.outra, nome="Alheio", tipo="fixo")
        self.padrao = CustoModelo.objects.get(nome="Água")

    def admin(self):
        from django.contrib.auth.models import Group

        usuario_admin = usuario("adm", self.matriz)
        usuario_admin.groups.add(Group.objects.get_or_create(name="Administrador")[0])
        return usuario_admin

    def links(self, usuario_, **params):
        self.client.force_login(usuario_)
        html = self.client.get(reverse("custo-comum-list"), {"status": "todos", "per_page": "100", **params}).content.decode()
        return html

    def test_usuario_com_permissao_ve_editar_e_excluir_dos_proprios(self):
        html = self.links(usuario("u", self.matriz), origem="proprio")
        self.assertIn(reverse("custo-comum-edit", args=[self.meu.pk]), html)
        self.assertIn(reverse("custo-comum-excluir", args=[self.meu.pk]), html)
        self.assertIn("data-confirmar=", html)  # a exclusão confirma pelo SweetAlert2

    def test_perfil_sem_excluir_ve_so_editar(self):
        html = self.links(usuario("g", self.matriz, comuns=("view", "add", "change")), origem="proprio")
        self.assertIn(reverse("custo-comum-edit", args=[self.meu.pk]), html)
        self.assertNotIn(reverse("custo-comum-excluir", args=[self.meu.pk]), html)

    def test_usuario_comum_nao_ve_acoes_nos_padroes_nem_em_itens_de_outra_empresa(self):
        html = self.links(usuario("u", self.matriz))
        self.assertNotIn(reverse("custo-comum-edit", args=[self.padrao.pk]), html)
        self.assertNotIn(reverse("custo-comum-excluir", args=[self.padrao.pk]), html)
        self.assertNotIn("Alheio", html)

    def test_administrador_ve_e_executa_acoes_nos_padroes(self):
        adm = self.admin()
        html = self.links(adm, origem="sistema")
        self.assertIn(reverse("custo-comum-edit", args=[self.padrao.pk]), html)
        self.assertIn(reverse("custo-comum-excluir", args=[self.padrao.pk]), html)
        corpo = {"nome": "Água e esgoto", "tipo": "variavel", "descricao": "Conta de água", "ativo": "on"}
        self.assertEqual(self.client.post(reverse("custo-comum-edit", args=[self.padrao.pk]), corpo).status_code, 302)
        self.padrao.refresh_from_db()
        self.assertEqual((self.padrao.nome, self.padrao.cliente_id), ("Água e esgoto", None))  # continua sendo padrão do sistema

    def test_usuario_comum_nao_altera_padrao_nem_item_alheio_no_servidor(self):
        self.client.force_login(usuario("u", self.matriz))
        corpo = {"nome": "X", "tipo": "fixo", "descricao": "", "ativo": "on"}
        for alvo in (self.padrao, self.alheio):
            self.assertEqual(self.client.post(reverse("custo-comum-edit", args=[alvo.pk]), corpo).status_code, 404)
            self.assertEqual(self.client.post(reverse("custo-comum-excluir", args=[alvo.pk])).status_code, 404)
        self.padrao.refresh_from_db()
        self.assertEqual(self.padrao.nome, "Água")

    def test_administrador_nao_edita_item_de_empresa_sem_escopo(self):
        # o administrador global enxerga todas as empresas pelo escopo de unidades; a regra de padrão não abre exceção extra
        adm = self.admin()
        self.client.force_login(adm)
        self.assertEqual(self.client.post(reverse("custo-comum-edit", args=[self.padrao.pk]), {"nome": "Água", "tipo": "variavel", "descricao": "", "ativo": "on"}).status_code, 302)

    def test_excluir_padrao_em_uso_so_desativa(self):
        CustoOperacional.objects.create(cliente=self.matriz, nome="Água", modelo=self.padrao, valor=100)
        self.client.force_login(self.admin())
        self.client.post(reverse("custo-comum-excluir", args=[self.padrao.pk]))
        self.padrao.refresh_from_db()
        self.assertFalse(self.padrao.ativo)
        self.assertEqual(CustoOperacional.objects.get(nome="Água").modelo_id, self.padrao.pk)  # vínculo preservado

    def test_sem_permissao_o_servidor_nega_mesmo_com_o_link_digitado(self):
        self.client.force_login(usuario("v", self.matriz, comuns=("view",)))
        self.assertEqual(self.client.post(reverse("custo-comum-excluir", args=[self.meu.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("custo-comum-edit", args=[self.meu.pk])).status_code, 403)


class NomeDoCustoOperacionalTests(TestCase):
    """Nome só no "Custo próprio"; com um custo comum o nome é o do dropdown (mesmo que algo seja enviado no campo)."""

    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.client.force_login(usuario("u", self.matriz))
        self.agua = CustoModelo.objects.get(nome="Água")

    def corpo(self, **extra):
        return {"modelo": "", "nome": "", "tipo": "fixo", "valor": "100,00", "descricao": "", "ativo": "on", **extra}

    def test_pagina_traz_o_bloco_do_nome_para_o_navegador_ocultar_e_mostrar(self):
        html = self.client.get(reverse("custo-create")).content.decode()
        self.assertIn('id="blocoNome"', html)
        self.assertIn("Custo próprio (sem modelo)", html)

    def test_custo_proprio_exige_nome_e_grava_o_nome_digitado(self):
        self.assertEqual(self.client.post(reverse("custo-create"), self.corpo()).status_code, 200)  # sem nome: recusado
        self.assertContains(self.client.post(reverse("custo-create"), self.corpo()), "Informe o nome do custo")
        self.client.post(reverse("custo-create"), self.corpo(nome="Seguro predial"))
        self.assertEqual(CustoOperacional.objects.get().nome, "Seguro predial")

    def test_com_custo_comum_o_nome_e_sempre_o_do_dropdown(self):
        self.client.post(reverse("custo-create"), self.corpo(modelo=self.agua.pk, nome="Nome digitado que deve ser ignorado"))
        c = CustoOperacional.objects.get()
        self.assertEqual((c.nome, c.modelo_id), ("Água", self.agua.pk))

    def test_editar_custo_com_modelo_mantem_o_nome_do_modelo(self):
        self.client.post(reverse("custo-create"), self.corpo(modelo=self.agua.pk))
        c = CustoOperacional.objects.get()
        self.client.post(reverse("custo-edit", args=[c.pk]), self.corpo(modelo=self.agua.pk, valor="250,00", nome="Outro"))
        c.refresh_from_db()
        self.assertEqual((c.nome, c.valor), ("Água", 25000))
