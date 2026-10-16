from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.ficha.models import Ficha
from web_project.testing import habilitar_modulo

from .models import Produto


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    habilitar_modulo(c, "produto")
    return c


def usuario(nome, c, acoes=("view", "add", "change", "delete"), filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="produto", codename__in=[f"{a}_produto" for a in acoes]))
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


class ProdutoTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.bebidas = Categoria.objects.create(cliente=self.matriz, nome="Bebidas")
        self.doces = Categoria.objects.create(cliente=self.matriz, nome="Doces")
        self.user = usuario("u", self.matriz)
        self.client.force_login(self.user)
        self.suco = Produto.objects.create(cliente=self.matriz, nome="Suco", categoria=self.bebidas, descricao="Natural")
        self.bolo = Produto.objects.create(cliente=self.matriz, nome="Bolo", categoria=self.doces)

    def nomes(self, **p):
        return [x.nome for x in self.client.get(reverse("produto"), p).context["produtos"]]

    def test_lista_filtros_e_ordenacao(self):
        self.assertEqual(self.nomes(), ["Bolo", "Suco"])
        self.assertEqual(self.nomes(sort="nome", dir="desc"), ["Suco", "Bolo"])
        self.assertEqual(self.nomes(sort="categoria"), ["Suco", "Bolo"])  # Bebidas < Doces
        self.assertEqual(self.nomes(categoria=self.doces.pk), ["Bolo"])
        self.assertEqual(self.nomes(q="natural"), ["Suco"])
        self.assertEqual(self.nomes(sort="drop table"), ["Bolo", "Suco"])  # coluna fora da lista: padrão
        self.bolo.ativo = False
        self.bolo.save()
        self.assertEqual(self.nomes(), ["Suco"])
        self.assertEqual(self.nomes(status="inativo"), ["Bolo"])
        self.assertEqual(self.nomes(status="todos"), ["Bolo", "Suco"])

    def test_paginacao_e_per_page_invalido(self):
        for i in range(30):
            Produto.objects.create(cliente=self.matriz, nome=f"P{i:02}", categoria=self.bebidas)
        resp = self.client.get(reverse("produto"), {"per_page": "1000"})
        self.assertEqual(len(resp.context["produtos"]), 25)  # valor fora das opções -> padrão
        self.assertEqual(len(self.client.get(reverse("produto"), {"per_page": "10", "page": "2"}).context["produtos"]), 10)

    def test_criar_e_salvar_e_adicionar_outro(self):
        dados = {"nome": "Torta", "categoria": self.doces.pk, "descricao": "", "ativo": "on"}
        resp = self.client.post(reverse("produto-create"), {**dados, "salvar_e_novo": "1"})
        self.assertRedirects(resp, reverse("produto-create"), fetch_redirect_response=False)
        torta = Produto.objects.get(nome="Torta")
        self.assertEqual(torta.cliente_id, self.matriz.pk)
        self.assertRedirects(self.client.post(reverse("produto-create"), {**dados, "nome": "Pudim"}), reverse("produto"), fetch_redirect_response=False)

    def test_categoria_invalida_e_recusada(self):
        inativa = Categoria.objects.create(cliente=self.matriz, nome="Velha", ativo=False)
        alheia = Categoria.objects.create(cliente=self.outra, nome="Alheia")
        for categoria in (inativa, alheia):
            resp = self.client.post(reverse("produto-create"), {"nome": "X", "categoria": categoria.pk, "ativo": "on"})
            self.assertEqual(resp.status_code, 200)
        self.assertFalse(Produto.objects.filter(nome="X").exists())

    def test_nome_duplicado_na_unidade(self):
        resp = self.client.post(reverse("produto-create"), {"nome": "suco", "categoria": self.bebidas.pk, "ativo": "on"})
        self.assertEqual(resp.status_code, 200)

    def test_editar_mantem_dono_e_categoria_inativa_atual(self):
        self.bebidas.ativo = False
        self.bebidas.save()
        resp = self.client.post(
            reverse("produto-edit", args=[self.suco.pk]),
            {"nome": "Suco de uva", "categoria": self.bebidas.pk, "descricao": "", "ativo": "on"},
        )
        self.assertEqual(resp.status_code, 302)
        self.suco.refresh_from_db()
        self.assertEqual((self.suco.nome, self.suco.cliente_id), ("Suco de uva", self.matriz.pk))

    def test_excluir_sem_uso_apaga_e_com_ficha_so_desativa(self):
        self.client.post(reverse("produto-excluir", args=[self.bolo.pk]))
        self.assertFalse(Produto.objects.filter(pk=self.bolo.pk).exists())
        Ficha.objects.create(cliente=self.matriz, produto=self.suco, nome="F")
        self.client.post(reverse("produto-excluir", args=[self.suco.pk]))
        self.suco.refresh_from_db()
        self.assertFalse(self.suco.ativo)

    def test_permissoes(self):
        self.client.force_login(usuario("v", self.matriz, ("view",)))
        self.assertEqual(self.client.get(reverse("produto")).status_code, 200)
        self.assertEqual(self.client.get(reverse("produto-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("produto-edit", args=[self.suco.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("produto-excluir", args=[self.suco.pk])).status_code, 403)
        self.assertContains(self.client.get(reverse("produto")), "Suco")
        self.assertNotContains(self.client.get(reverse("produto")), reverse("produto-create"))

    def test_outra_empresa_da_404(self):
        alheio = Produto.objects.create(cliente=self.outra, nome="Alheio", categoria=Categoria.objects.create(cliente=self.outra, nome="C"))
        self.assertEqual(self.client.get(reverse("produto-edit", args=[alheio.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("produto-excluir", args=[alheio.pk])).status_code, 404)
        self.assertNotIn("Alheio", self.nomes())

    def test_matriz_e_filial(self):
        cat_f = Categoria.objects.create(cliente=self.filial, nome="CF")
        da_filial = Produto.objects.create(cliente=self.filial, nome="DaFilial", categoria=cat_f)
        self.assertNotIn("DaFilial", self.nomes())  # sem permissão de filiais
        self.client.force_login(usuario("m", self.matriz, filiais=("view", "change")))
        self.assertIn("DaFilial", self.nomes())
        self.assertEqual(self.nomes(unidade=self.filial.pk), ["DaFilial"])
        self.assertEqual(self.client.get(reverse("produto-edit", args=[da_filial.pk])).status_code, 200)
        self.client.force_login(usuario("f", self.filial, filiais=("view", "change", "delete")))
        self.assertEqual(self.nomes(), ["DaFilial"])  # filial não vê a matriz
        self.assertEqual(self.client.get(reverse("produto-edit", args=[self.suco.pk])).status_code, 404)

    def test_exportacao_respeita_filtro_e_escopo(self):
        Produto.objects.create(cliente=self.outra, nome="Alheio", categoria=Categoria.objects.create(cliente=self.outra, nome="C"))
        resp = self.client.get(reverse("produto"), {"export": "xlsx", "categoria": self.doces.pk})
        self.assertIn("spreadsheetml", resp["Content-Type"])
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([l[0] for l in linhas[1:]], ["Bolo"])  # só o filtrado, nunca de outra empresa
        pdf = self.client.get(reverse("produto"), {"export": "pdf"})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_exportacao_neutraliza_formula(self):
        Produto.objects.create(cliente=self.matriz, nome="=CMD()", categoria=self.bebidas)
        resp = self.client.get(reverse("produto"), {"export": "xlsx", "q": "CMD"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual(linhas[1][0], "'=CMD()")

    def test_exportacao_exige_permissao_de_ver(self):
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        self.assertEqual(self.client.get(reverse("produto"), {"export": "xlsx"}).status_code, 403)


class CodigoBarrasTests(ProdutoTests):
    """Código de barras e QR Code: gerado no cadastro, único por unidade, com imagens SVG e etiqueta."""

    def test_codigo_interno_e_ean13_valido(self):
        from .codigos import ean13_valido

        self.assertTrue(ean13_valido(self.suco.codigo_barras))
        self.assertTrue(self.suco.codigo_barras.startswith("2"))
        self.assertNotEqual(self.suco.codigo_barras, self.bolo.codigo_barras)

    def test_cadastro_gera_codigo_ou_aceita_o_do_fabricante(self):
        dados = {"nome": "Água", "categoria": self.bebidas.pk, "descricao": "", "codigo_barras": "", "ativo": "on"}
        self.client.post(reverse("produto-create"), dados)
        self.assertTrue(Produto.objects.get(nome="Água").codigo_barras)
        self.client.post(reverse("produto-create"), {**dados, "nome": "Cola", "codigo_barras": "7891234567895"})
        self.assertEqual(Produto.objects.get(nome="Cola").codigo_barras, "7891234567895")
        resposta = self.client.post(reverse("produto-create"), {**dados, "nome": "Cola 2", "codigo_barras": "7891234567895"})
        self.assertContains(resposta, "já pertence a outro produto")
        resposta = self.client.post(reverse("produto-create"), {**dados, "nome": "Cola 3", "codigo_barras": "ab cd!"})
        self.assertFalse(Produto.objects.filter(nome="Cola 3").exists())

    def test_editar_em_branco_mantem_o_codigo(self):
        antes = self.suco.codigo_barras
        self.client.post(reverse("produto-edit", args=[self.suco.pk]), {"nome": "Suco", "categoria": self.bebidas.pk, "descricao": "", "codigo_barras": "", "ativo": "on"})
        self.suco.refresh_from_db()
        self.assertEqual(self.suco.codigo_barras, antes)

    def test_imagens_svg_e_isolamento(self):
        for tipo in ("barras", "qr"):
            resposta = self.client.get(reverse("produto-codigo", args=[self.suco.pk, tipo]))
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(resposta["Content-Type"], "image/svg+xml")
            self.assertIn(b"<svg", resposta.content)
        externo = Produto.objects.create(cliente=self.outra, nome="X", categoria=Categoria.objects.create(cliente=self.outra, nome="C"))
        self.assertEqual(self.client.get(reverse("produto-codigo", args=[externo.pk, "qr"])).status_code, 404)

    def test_etiquetas_e_busca_por_codigo(self):
        resposta = self.client.get(reverse("produto-etiquetas"), {"ids": f"{self.suco.pk}"})
        self.assertContains(resposta, "Suco")
        self.assertNotContains(resposta, "Bolo")
        self.assertEqual(self.nomes(q=self.bolo.codigo_barras), ["Bolo"])
