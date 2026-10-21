"""Serviço (produto do tipo serviço), tela de Serviços em Mão de obra e mão de obra na ficha técnica: segmentos, isolamento entre
unidades, exclusão segura e custo calculado no servidor."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.ficha.models import Ficha, FichaMaoDeObra
from apps.maodeobra.models import Apontamento, Colaborador
from apps.precificacao.models import Precificacao
from apps.precificacao.tests import nova_precificacao
from apps.produto.forms import ProdutoForm
from apps.produto.models import Produto
from web_project.testing import habilitar_modulo

from .tests import Base, V1, APIClient, Token, usuario

D = Decimal


class ServicoBase(Base):
    def setUp(self):
        super().setUp()
        for cli in (self.cliente, self.outro, self.produto_puro):
            for modulo in ("produto", "ficha", "insumo", "categoria", "precificacao"):
                habilitar_modulo(cli, modulo)
        self.cat = Categoria.objects.create(cliente=self.cliente, nome="Serviços")
        self.cat_b = Categoria.objects.create(cliente=self.outro, nome="Serviços")
        self.admin = usuario("adm", self.cliente, "Administrador")  # excluir é permissão do Administrador (o Gestor só vê/cria/altera)

    def novo_servico(self, nome="Instalação", cliente=None, categoria=None):
        cliente = cliente or self.cliente
        return Produto.objects.create(cliente=cliente, nome=nome, tipo=Produto.SERVICO, categoria=categoria or (self.cat if cliente == self.cliente else self.cat_b))

    def api(self, usuario):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")
        return api


class TipoPorSegmentoTests(ServicoBase):
    def test_segmento_define_o_tipo_e_ambos_escolhe(self):
        self.assertNotIn("tipo", ProdutoForm(cliente=self.produto_puro).fields)  # produto puro: sempre produto
        self.assertIn("tipo", ProdutoForm(cliente=self.cliente).fields)  # serviço e produto: escolhe
        self.assertEqual(ProdutoForm(cliente=self.produto_puro).instance.tipo, "produto")
        with self.assertRaises(ValidationError):  # vale também fora do formulário (modelo)
            Produto(cliente=self.produto_puro, nome="X", categoria=Categoria.objects.create(cliente=self.produto_puro, nome="C"), tipo="servico").full_clean()

    def test_produto_cadastrado_em_segmento_de_servico_puro_vira_servico(self):
        from apps.modulo.models import Segmento

        puro = Segmento.objects.get(slug="servico")
        self.cliente.segmento = puro
        self.cliente.save()
        self.assertNotIn("tipo", ProdutoForm(cliente=self.cliente).fields)
        self.assertEqual(ProdutoForm(cliente=self.cliente).instance.tipo, "servico")

    def test_listagem_filtra_por_tipo_e_api_expoe_o_tipo(self):
        self.novo_servico()
        Produto.objects.create(cliente=self.cliente, nome="Peça", categoria=self.cat)
        nomes = lambda r: [p["nome"] for p in r.json()["data"]["itens"]]  # noqa: E731
        self.assertEqual(nomes(self.api(self.gestor).get(f"{V1}/produtos/?tipo=servico")), ["Instalação"])
        self.assertEqual(nomes(self.api(self.gestor).get(f"{V1}/produtos/?tipo=produto")), ["Peça"])
        r = self.api(self.gestor).post(f"{V1}/produtos/", {"nome": "Visita", "categoria": self.cat.pk, "tipo": "servico"}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["tipo"]), (201, "servico"), r.content[:200])


class ServicosEmMaoDeObraTests(ServicoBase):
    def test_cadastrar_consultar_atualizar_e_excluir(self):
        r = self.client.post(reverse("servico-create"), {"nome": "Manutenção", "categoria": self.cat.pk, "descricao": "preventiva", "ativo": "on"})
        self.assertEqual(r.status_code, 302, r.content[:200])
        servico = Produto.objects.get(nome="Manutenção")
        self.assertEqual((servico.tipo, servico.cliente_id), ("servico", self.cliente.pk))
        self.assertContains(self.client.get(reverse("servico-list")), "Manutenção")
        self.client.post(reverse("servico-edit", args=[servico.pk]), {"nome": "Manutenção geral", "categoria": self.cat.pk, "ativo": "on"})
        servico.refresh_from_db()
        self.assertEqual(servico.nome, "Manutenção geral")
        self.client.force_login(self.admin)
        self.client.post(reverse("servico-excluir", args=[servico.pk]))
        self.assertFalse(Produto.objects.filter(pk=servico.pk).exists())

    def test_servico_em_uso_so_e_desativado(self):
        servico = self.novo_servico()
        Apontamento.objects.create(cliente=self.cliente, colaborador=self.ana, servico=servico, data="2026-10-12", tipo="dia", dias=D("1"))
        self.client.force_login(self.admin)
        self.client.post(reverse("servico-excluir", args=[servico.pk]))
        servico.refresh_from_db()
        self.assertFalse(servico.ativo)

    def test_isolamento_entre_unidades_e_so_servicos(self):
        alheio = self.novo_servico("Alheio", cliente=self.outro)
        produto = Produto.objects.create(cliente=self.cliente, nome="Peça", categoria=self.cat)
        self.assertNotContains(self.client.get(reverse("servico-list")), "Alheio")
        self.assertNotContains(self.client.get(reverse("servico-list")), "Peça")
        self.client.force_login(self.admin)
        for nome in ("servico-edit", "servico-excluir"):
            for pk in (alheio.pk, produto.pk):  # de outra unidade e produto comum: 404
                metodo = self.client.get if nome == "servico-edit" else self.client.post
                self.assertEqual(metodo(reverse(nome, args=[pk])).status_code, 404, (nome, pk))
        self.assertTrue(Produto.objects.filter(pk=alheio.pk).exists())

    def test_permissoes_e_segmento(self):
        self.client.force_login(self.operador)  # Operador cria, mas não exclui
        servico = self.novo_servico()
        self.assertEqual(self.client.post(reverse("servico-excluir", args=[servico.pk])).status_code, 403)
        self.client.force_login(self.gestor_padaria)  # segmento de produto: sem mão de obra
        self.assertEqual(self.client.get(reverse("servico-list")).status_code, 403)

    def test_apontamento_so_oferece_servicos_ativos_da_unidade(self):
        servico = self.novo_servico()
        Produto.objects.create(cliente=self.cliente, nome="Peça", categoria=self.cat)
        self.novo_servico("Alheio", cliente=self.outro)
        r = self.client.get(reverse("apontamento-create"))
        self.assertEqual(list(r.context["form"].fields["servico"].queryset), [servico])


class FichaDeServicoTests(ServicoBase):
    def dados(self, produto, **extra):
        base = {"produto": produto.pk, "ativo": "on", "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
                "mao_de_obra-TOTAL_FORMS": "1", "mao_de_obra-INITIAL_FORMS": "0", "mao_de_obra-MIN_NUM_FORMS": "0", "mao_de_obra-MAX_NUM_FORMS": "1000",
                "mao_de_obra-0-colaborador": self.ana.pk, "mao_de_obra-0-horas": "1,5"}
        return {**base, **extra}

    def test_ficha_so_com_mao_de_obra_sem_insumos(self):
        servico = self.novo_servico()
        r = self.client.post(reverse("ficha-create"), self.dados(servico))
        self.assertEqual(r.status_code, 302, r.content[:300])
        ficha = Ficha.objects.get()
        linha = ficha.mao_de_obra.get()
        self.assertEqual((linha.colaborador_id, linha.horas, linha.custo, ficha.custo_total), (self.ana.pk, D("1.5"), 3000, 3000))  # R$ 20,00/h × 1,5 h
        self.assertEqual(ficha.itens.count(), 0)

    def test_ficha_vazia_e_permitida_no_segmento_de_servico_e_insumo_continua_obrigatorio_no_de_produto(self):
        servico = self.novo_servico()
        dados = self.dados(servico, **{"mao_de_obra-TOTAL_FORMS": "0"})
        self.assertEqual(self.client.post(reverse("ficha-create"), dados).status_code, 302)
        self.assertEqual(Ficha.objects.get().custo_total, 0)
        # segmento de produto: ao menos um insumo, como sempre
        self.client.force_login(self.gestor_padaria)
        cat = Categoria.objects.create(cliente=self.produto_puro, nome="Pães")
        pao = Produto.objects.create(cliente=self.produto_puro, nome="Pão", categoria=cat)
        r = self.client.post(reverse("ficha-create"), {"produto": pao.pk, "ativo": "on", "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Inclua ao menos um insumo")
        self.assertNotContains(r, "tabelaMaoDeObra")  # segmento de produto não vê mão de obra

    def test_colaborador_repetido_ou_de_outra_unidade_e_recusado(self):
        servico = self.novo_servico()
        repetido = self.dados(servico, **{"mao_de_obra-TOTAL_FORMS": "2", "mao_de_obra-1-colaborador": self.ana.pk, "mao_de_obra-1-horas": "1"})
        self.assertEqual(self.client.post(reverse("ficha-create"), repetido).status_code, 200)
        alheio = Colaborador.objects.create(cliente=self.outro, nome="Beto", valor_hora=1000)
        self.assertEqual(self.client.post(reverse("ficha-create"), self.dados(servico, **{"mao_de_obra-0-colaborador": alheio.pk})).status_code, 200)
        self.assertFalse(Ficha.objects.exists())
        for horas in ("0", "-1", "abc", ""):
            self.assertEqual(self.client.post(reverse("ficha-create"), self.dados(servico, **{"mao_de_obra-0-horas": horas})).status_code, 200, horas)

    def test_mudar_o_valor_da_hora_recalcula_ficha_e_precificacao(self):
        servico = self.novo_servico()
        self.client.post(reverse("ficha-create"), self.dados(servico))
        ficha = Ficha.objects.get()
        preco = nova_precificacao(self.cliente, ficha, percentual_lucro=D("100"))
        self.assertEqual(preco.preco_venda, 6000)
        self.ana.valor_hora = 4000
        self.ana.save()
        ficha.refresh_from_db()
        preco.refresh_from_db()
        self.assertEqual((ficha.custo_total, preco.preco_venda), (6000, 12000))
        self.assertTrue(Precificacao.objects.filter(pk=preco.pk).exists())

    def test_colaborador_usado_em_ficha_nao_e_apagado(self):
        servico = self.novo_servico()
        self.client.post(reverse("ficha-create"), self.dados(servico))
        self.client.force_login(self.admin)
        self.client.post(reverse("colaborador-excluir", args=[self.ana.pk]))
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.ativo)
        self.assertTrue(FichaMaoDeObra.objects.exists())

    def test_previa_de_custo_inclui_mao_de_obra_e_respeita_a_unidade(self):
        alheio = Colaborador.objects.create(cliente=self.outro, nome="Beto", valor_hora=1000)
        corpo = {"itens": [], "mao_de_obra": [{"colaborador": self.ana.pk, "horas": "2"}, {"colaborador": alheio.pk, "horas": "2"}]}
        r = self.client.post(reverse("ficha-calcular"), corpo, content_type="application/json").json()
        self.assertEqual(r["mao_de_obra"][0]["custo"], "R$ 40,00")
        self.assertEqual(r["mao_de_obra"][1]["custo"], "—")  # colaborador de outra unidade não é calculado
        self.assertEqual(r["total"], "R$ 40,00")

    def test_api_da_ficha_com_mao_de_obra(self):
        servico = self.novo_servico()
        api = self.api(self.gestor)
        r = api.post(f"{V1}/fichas-tecnicas/", {"produto": servico.pk, "mao_de_obra": [{"colaborador": self.ana.pk, "horas": "2"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        dado = r.json()["data"]
        self.assertEqual((dado["custo_total"], dado["mao_de_obra"][0]["custo"], dado["itens"]), (4000, 4000, []))
        # PATCH sem mão de obra mantém; lista vazia remove
        self.assertEqual(api.patch(f"{V1}/fichas-tecnicas/{dado['id']}/", {"ativo": False}, format="json").json()["data"]["custo_total"], 4000)
        self.assertEqual(api.patch(f"{V1}/fichas-tecnicas/{dado['id']}/", {"mao_de_obra": []}, format="json").json()["data"]["custo_total"], 0)
        # produto de segmento sem mão de obra: recusa
        padaria = self.api(self.gestor_padaria)
        pao = Produto.objects.create(cliente=self.produto_puro, nome="Pão", categoria=Categoria.objects.create(cliente=self.produto_puro, nome="P"))
        self.assertEqual(padaria.post(f"{V1}/fichas-tecnicas/", {"produto": pao.pk, "mao_de_obra": [{"colaborador": self.ana.pk, "horas": "1"}]}, format="json").status_code, 400)
