import json
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.insumo.tests import medida, novo_insumo
from apps.processado.services import mensagem_insumo_em_uso
from apps.processado.tests import criar as criar_processado
from apps.produto.models import Produto
from web_project.testing import habilitar_modulo

from .models import Ficha, FichaItem
from .services import salvar_ficha


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in ("insumo", "processado", "produto", "ficha"):
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, acoes=("view", "add", "change", "delete"), filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="ficha", codename__in=[f"{a}_ficha" for a in acoes]))
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


def produto(c, nome="Bolo"):
    categoria, _ = Categoria.objects.get_or_create(cliente=c, nome="Doces")
    return Produto.objects.create(cliente=c, nome=nome, categoria=categoria)


def criar(c, nome, itens, prod=None):
    """Cria ficha pelo serviço real. itens: [(insumo, sigla, quantidade)]."""
    ficha = Ficha(cliente=c, produto=prod or produto(c, f"P-{nome}"), nome=nome)
    linhas = [FichaItem(insumo=i, medida=medida(s), quantidade=Decimal(q)) for i, s, q in itens]
    salvar_ficha(None, ficha, linhas)
    ficha.refresh_from_db()
    return ficha


class CalculoETransacaoTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 500)  # R$ 5,00/kg
        self.ovo = novo_insumo(self.c, "Ovo", "un", "12", 1200)  # R$ 1,00/un

    def test_custos_dos_itens_e_total(self):
        ficha = criar(self.c, "Bolo", [(self.farinha, "g", "250"), (self.ovo, "un", "3")])
        self.assertEqual({i.insumo.nome: i.custo for i in ficha.itens.all()}, {"Farinha": 125, "Ovo": 300})
        self.assertEqual(ficha.custo_total, 425)

    def test_alterar_insumo_recalcula_a_ficha(self):
        ficha = criar(self.c, "Bolo", [(self.farinha, "g", "250")])
        self.farinha.preco_pago = 1000
        self.farinha.save()
        ficha.refresh_from_db()
        self.assertEqual(ficha.custo_total, 250)

    def test_cadeia_insumo_processado_ficha(self):
        massa = criar_processado(self.c, "Massa", [(self.farinha, "kg", "1")], "1", "kg")  # R$ 5,00/kg
        ficha = criar(self.c, "Torta", [(massa.insumo, "g", "500")])
        self.assertEqual(ficha.custo_total, 250)
        self.farinha.preco_pago = 1000  # farinha dobra -> massa dobra -> ficha dobra
        self.farinha.save()
        ficha.refresh_from_db()
        self.assertEqual(ficha.custo_total, 500)

    def test_alterar_unidade_do_insumo_recalcula(self):
        ficha = criar(self.c, "Bolo", [(self.farinha, "g", "500")])
        self.farinha.quantidade = Decimal("2")  # R$ 5,00 por 2 kg
        self.farinha.save()
        ficha.refresh_from_db()
        self.assertEqual(ficha.custo_total, 125)

    def test_unidade_incompativel_recusada_e_nada_gravado(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            criar(self.c, "Ruim", [(self.farinha, "L", "1")])
        self.assertFalse(Ficha.objects.filter(nome="Ruim").exists())

    def test_insumo_em_ficha_nao_pode_ser_excluido_nem_trocar_dimensao(self):
        criar(self.c, "Bolo", [(self.farinha, "g", "250")])
        self.assertIn("Bolo", mensagem_insumo_em_uso(self.farinha))

    def test_recalculo_geral_e_idempotente(self):
        ficha = criar(self.c, "Bolo", [(self.farinha, "g", "250")])
        Ficha.objects.filter(pk=ficha.pk).update(custo_total=1)  # estado "velho"
        call_command("recalcular_custos", verbosity=0)
        call_command("recalcular_custos", verbosity=0)
        ficha.refresh_from_db()
        self.assertEqual(ficha.custo_total, 125)

    def test_ficha_de_outro_cliente_nao_e_tocada(self):
        outro = cliente("Outro", "34028316000103")
        f2 = novo_insumo(outro, "Farinha", "kg", "1", 500)
        ficha_outro = criar(outro, "Bolo", [(f2, "g", "250")])
        self.farinha.preco_pago = 9999
        self.farinha.save()
        ficha_outro.refresh_from_db()
        self.assertEqual(ficha_outro.custo_total, 125)


class TelasFichaTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 500)
        self.prod = produto(self.matriz)

    def dados(self, nome="Bolo simples", itens=(("farinha", "g", "250"),), **extra):
        base = {
            "nome": nome, "produto": self.prod.pk, "ativo": "on",
            "itens-TOTAL_FORMS": str(len(itens)), "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
        }
        for i, (insumo, sigla, qtd) in enumerate(itens):
            ins = self.farinha if insumo == "farinha" else insumo
            base.update({f"itens-{i}-insumo": ins.pk, f"itens-{i}-medida": medida(sigla).pk, f"itens-{i}-quantidade": qtd})
        return {**base, **extra}

    def nomes(self, **p):
        return [f.nome for f in self.client.get(reverse("ficha"), p).context["fichas"]]

    def test_criar_pela_tela_mostra_custo(self):
        resp = self.client.post(reverse("ficha-create"), self.dados())
        self.assertRedirects(resp, reverse("ficha"), fetch_redirect_response=False)
        ficha = Ficha.objects.get()
        self.assertEqual((ficha.cliente_id, ficha.custo_total), (self.matriz.pk, 125))
        self.assertContains(self.client.get(reverse("ficha")), "R$ 1,25")
        edicao = self.client.get(reverse("ficha-edit", args=[ficha.pk]))
        self.assertContains(edicao, "R$ 1,25")  # topo e rodapé (classe total-ficha)
        self.assertEqual(edicao.content.decode().count("total-ficha"), 2 + edicao.content.decode().count("querySelectorAll('.total-ficha')"))

    def test_salvar_e_adicionar_outra(self):
        resp = self.client.post(reverse("ficha-create"), self.dados(salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("ficha-create"), fetch_redirect_response=False)

    def test_editar_recalcula_e_nao_troca_dono(self):
        self.client.post(reverse("ficha-create"), self.dados())
        ficha = Ficha.objects.get()
        item = ficha.itens.get()
        edicao = {
            "nome": "Bolo simples", "produto": self.prod.pk, "ativo": "on",
            "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "1", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-id": item.pk, "itens-0-ficha": ficha.pk, "itens-0-insumo": self.farinha.pk,
            "itens-0-medida": medida("kg").pk, "itens-0-quantidade": "2",
        }
        self.assertEqual(self.client.post(reverse("ficha-edit", args=[ficha.pk]), edicao).status_code, 302)
        ficha.refresh_from_db()
        self.assertEqual((ficha.custo_total, ficha.cliente_id), (1000, self.matriz.pk))

    def test_validacoes(self):
        casos = [
            self.dados(itens=()),  # sem itens
            self.dados(itens=(("farinha", "g", "0"),)),  # quantidade zero
            self.dados(itens=(("farinha", "g", "-5"),)),
            self.dados(itens=(("farinha", "g", "1"), ("farinha", "kg", "1"))),  # insumo repetido
            self.dados(**{"produto": ""}),
        ]
        for dados in casos:
            self.assertEqual(self.client.post(reverse("ficha-create"), dados).status_code, 200)
        self.assertFalse(Ficha.objects.exists())

    def test_insumo_ou_produto_de_outra_unidade_recusado(self):
        alheio = novo_insumo(self.outra, "Alheio", "kg", "1", 100)
        self.assertEqual(self.client.post(reverse("ficha-create"), self.dados(itens=((alheio, "kg", "1"),))).status_code, 200)
        prod_alheio = produto(self.outra, "PA")
        self.assertEqual(self.client.post(reverse("ficha-create"), self.dados(produto=prod_alheio.pk)).status_code, 200)
        self.assertFalse(Ficha.objects.exists())

    def test_filtros_e_ordenacao(self):
        outro_prod = produto(self.matriz, "Torta")
        a = criar(self.matriz, "A", [(self.farinha, "g", "100")], self.prod)
        b = criar(self.matriz, "B", [(self.farinha, "g", "900")], outro_prod)
        self.assertEqual(self.nomes(), ["A", "B"])
        self.assertEqual(self.nomes(sort="custo", dir="desc"), ["B", "A"])
        self.assertEqual(self.nomes(produto=outro_prod.pk), ["B"])
        self.assertEqual(self.nomes(q="torta"), ["B"])
        b.ativo = False
        b.save()
        self.assertEqual(self.nomes(), ["A"])
        self.assertEqual(self.nomes(status="inativo"), ["B"])
        self.assertEqual(a.custo_total, 50)

    def test_excluir_apaga(self):
        f = criar(self.matriz, "A", [(self.farinha, "g", "100")], self.prod)
        self.client.post(reverse("ficha-excluir", args=[f.pk]))
        self.assertFalse(Ficha.objects.filter(pk=f.pk).exists())
        self.assertFalse(FichaItem.objects.exists())
        self.assertTrue(Produto.objects.filter(pk=self.prod.pk).exists())  # sem cascata para cima

    def test_permissoes_e_tenant(self):
        f = criar(self.matriz, "A", [(self.farinha, "g", "100")], self.prod)
        alheia = criar(self.outra, "Z", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        self.assertEqual(self.client.get(reverse("ficha-edit", args=[alheia.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("ficha-excluir", args=[alheia.pk])).status_code, 404)
        self.assertNotIn("Z", self.nomes())
        self.client.force_login(usuario("v", self.matriz, ("view",)))
        self.assertEqual(self.client.get(reverse("ficha-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("ficha-edit", args=[f.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("ficha-excluir", args=[f.pk])).status_code, 403)

    def test_matriz_alcanca_filial_com_permissao(self):
        prod_f = produto(self.filial, "PF")
        insumo_f = novo_insumo(self.filial, "InsF", "kg", "1", 100)
        da_filial = criar(self.filial, "DaFilial", [(insumo_f, "g", "100")], prod_f)
        self.assertNotIn("DaFilial", self.nomes())
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change")))
        self.assertIn("DaFilial", self.nomes())
        self.assertEqual(self.client.get(reverse("ficha-edit", args=[da_filial.pk])).status_code, 200)
        self.client.force_login(usuario("f", self.filial, filiais=("view", "change")))
        self.assertEqual(self.nomes(), ["DaFilial"])

    def test_exportacao(self):
        criar(self.matriz, "A", [(self.farinha, "g", "100")], self.prod)
        criar(self.outra, "Z", [(novo_insumo(self.outra, "X", "kg", "1", 100), "g", "100")])
        resp = self.client.get(reverse("ficha"), {"export": "xlsx"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([l[0] for l in linhas[1:]], ["A"])
        self.assertEqual((linhas[1][3], linhas[1][4]), (1, "R$ 0,50"))
        self.assertTrue(self.client.get(reverse("ficha"), {"export": "pdf"}).content.startswith(b"%PDF"))

    def post_calc(self, itens):
        return self.client.post(reverse("ficha-calcular"), data=json.dumps({"itens": itens}), content_type="application/json").json()

    def test_previa_do_servidor(self):
        d = self.post_calc([
            {"insumo": self.farinha.pk, "medida": medida("g").pk, "quantidade": "250"},
            {"insumo": self.farinha.pk, "medida": medida("kg").pk, "quantidade": "1,5"},
            {"insumo": "", "medida": "", "quantidade": ""},
            {"insumo": self.farinha.pk, "medida": medida("L").pk, "quantidade": "1"},  # incompatível
        ])
        self.assertEqual([i["custo"] for i in d["itens"]], ["R$ 1,25", "R$ 7,50", "—", "—"])
        self.assertIn("compat", d["itens"][3]["erro"])
        self.assertEqual(d["total"], "R$ 8,75")

    def test_previa_nao_enxerga_insumo_de_outro_tenant(self):
        alheio = novo_insumo(self.outra, "Alheio", "kg", "1", 100)
        d = self.post_calc([{"insumo": alheio.pk, "medida": medida("kg").pk, "quantidade": "1"}])
        self.assertEqual((d["itens"][0]["custo"], d["total"]), ("—", "R$ 0,00"))

    def test_previa_entradas_invalidas(self):
        resp = self.client.post(reverse("ficha-calcular"), data="não é json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        d = self.post_calc([{"insumo": self.farinha.pk, "medida": medida("g").pk, "quantidade": "abc"}])
        self.assertEqual(d["itens"][0]["erro"], "Informe um número válido.")
        self.assertEqual(self.client.get(reverse("ficha-calcular")).status_code, 405)

    def test_previa_exige_permissao(self):
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        resp = self.client.post(reverse("ficha-calcular"), data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 403)


class FichaSemNomeDigitadoTests(TestCase):
    """O nome da ficha deixou de ser digitado: é derivado do produto."""

    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 500)
        self.prod = produto(self.matriz, "Pizza")

    def corpo(self, **extra):
        base = {
            "produto": self.prod.pk, "ativo": "on", "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.farinha.pk, "itens-0-medida": medida("kg").pk, "itens-0-quantidade": "1",
        }
        return {**base, **extra}

    def test_formulario_nao_tem_campo_nome(self):
        html = self.client.get(reverse("ficha-create")).content.decode()
        self.assertNotIn('name="nome"', html)
        self.assertNotIn("Nome da ficha", html)

    def test_nome_derivado_do_produto_e_ignora_nome_enviado(self):
        self.client.post(reverse("ficha-create"), self.corpo(nome="Hackeado"))
        self.assertEqual(Ficha.objects.get().nome, "Pizza")

    def test_segunda_ficha_do_mesmo_produto_recebe_sufixo(self):
        self.client.post(reverse("ficha-create"), self.corpo())
        self.client.post(reverse("ficha-create"), self.corpo())
        self.assertEqual(sorted(Ficha.objects.values_list("nome", flat=True)), ["Pizza", "Pizza (2)"])

    def test_unidade_dos_itens_mostra_todas_as_opcoes_com_nome_completo(self):
        html = self.client.get(reverse("ficha-create")).content.decode()
        bloco = html[html.index('name="itens-0-medida"'):]
        bloco = bloco[:bloco.index("</select>")]
        # todas as unidades ativas do sistema, com o nome completo (e a sigla entre parênteses)
        from apps.medida.models import Medida

        for m in Medida.objects.filter(ativo=True):
            self.assertIn(f'value="{m.pk}">{m.medida} ({m.sigla})</option>', bloco)
        self.assertNotIn(">Kg</option>", bloco)  # não é mais só a sigla
        # quem escolhe a unidade é o usuário: a primeira opção ("Unidade...") é a única selecionada
        self.assertEqual(bloco.count("selected"), 1)
        self.assertIn('<option value="" selected>Unidade...</option>', bloco)
        self.assertNotIn("data-medida", html)  # escolher o insumo não muda a unidade
        self.assertNotIn("unidade-do-insumo.js", html)

    def test_cada_linha_tem_o_espaco_da_quantidade_por_extenso_e_a_previa_devolve_o_texto(self):
        html = self.client.get(reverse("ficha-create")).content.decode()
        self.assertGreaterEqual(html.count("extenso-item"), 2)  # linha inicial e modelo das linhas novas
        corpo = {"itens": [{"insumo": self.farinha.pk, "medida": medida("g").pk, "quantidade": "100"}, {"insumo": "", "medida": medida("kg").pk, "quantidade": "1"}]}
        itens = self.client.post(reverse("ficha-calcular"), corpo, content_type="application/json").json()["itens"]
        self.assertEqual((itens[0]["extenso"], itens[0]["custo"]), ("100 gramas", "R$ 0,50"))
        self.assertEqual((itens[1]["extenso"], itens[1]["custo"]), ("1 quilo", "—"))  # extenso mesmo sem insumo escolhido

    def test_custo_da_linha_e_quantidade_vezes_preco_na_unidade_escolhida(self):
        # Abacaxi a R$ 5,00 por unidade: 2 unidades = R$ 10,00, na prévia e ao gravar
        abacaxi = novo_insumo(self.matriz, "Abacaxi", "un", "1", 500)
        corpo = {"itens": [{"insumo": abacaxi.pk, "medida": medida("un").pk, "quantidade": "2"}]}
        dados = self.client.post(reverse("ficha-calcular"), corpo, content_type="application/json").json()
        self.assertEqual((dados["itens"][0]["custo"], dados["total"]), ("R$ 10,00", "R$ 10,00"))
        self.client.post(reverse("ficha-create"), self.corpo(**{"itens-0-insumo": abacaxi.pk, "itens-0-medida": medida("un").pk, "itens-0-quantidade": "2"}))
        self.assertEqual(Ficha.objects.get().custo_total, 1000)

    def test_servidor_continua_aceitando_gramas(self):
        self.client.post(reverse("ficha-create"), self.corpo(**{"itens-0-medida": medida("g").pk, "itens-0-quantidade": "250"}))
        self.assertEqual(Ficha.objects.get().custo_total, 125)
