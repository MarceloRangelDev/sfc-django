"""Canais de venda e formas de pagamento GLOBAIS: cadastro só pelo administrador do sistema, liberação por segmento, uso (precificação e PDV)
sem poder alterar, e isolamento entre clientes. Ver web_project/globais.py."""
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.canal.models import CanalVenda
from apps.canal.services import salvar_canal
from apps.cliente.models import Cliente
from apps.modulo.models import Segmento
from apps.ficha.tests import criar as criar_ficha, produto as criar_produto
from apps.pdvconfig.models import FormaPagamento
from apps.precificacao.models import Precificacao
from apps.precificacao.tests import nova_precificacao
from apps.precificacao.forms import PrecificacaoForm
from web_project.regras import RegraNegocioError

from . import services as s
from .tests import PdvBase, Req, novo_usuario

V1 = "/api/v1"
D = Decimal


class GlobaisBase(PdvBase):
    def setUp(self):
        super().setUp()
        self.produto = Segmento.objects.get(slug="produto")
        self.servico = Segmento.objects.get(slug="servico")
        self.sistema = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="04252011000110", nome_razao_social="Sistema", sistema=True, segmento=self.produto)
        self.admin = novo_usuario("adm", self.sistema, "Administrador")
        # `self.cliente` e `self.gestor` já estão no segmento "produto"; `self.outro` passa a ser de "servico"
        self.outro.segmento = self.servico
        self.outro.save()
        self.client.force_login(self.admin)

    def api(self, usuario):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")
        return api

    def forma_global(self, segmentos=None, **extra):
        f = FormaPagamento.objects.create(cliente=self.sistema, nome="PIX Global", tipo="pix", eh_global=True, **extra)
        f.segmentos.set(segmentos if segmentos is not None else [self.produto])
        return f

    def canal_global(self, segmentos=None):
        c = CanalVenda.objects.create(cliente=self.sistema, nome="Marketplace", descricao="global", eh_global=True)
        c.segmentos.set(segmentos if segmentos is not None else [self.produto])
        return c


class FormaGlobalTests(GlobaisBase):
    def post_forma(self, **extra):
        dados = {"nome": "Vale Global", "tipo": "outro", "taxa_modo": "sem_taxa", "ativo": "on", **extra}
        return self.client.post(reverse("forma-pagamento-create"), dados)

    def test_admin_cria_forma_global_com_segmentos_e_exige_segmento(self):
        self.assertEqual(self.post_forma(eh_global="on").status_code, 200)  # sem segmento: recusa
        self.assertFalse(FormaPagamento.objects.filter(nome="Vale Global").exists())
        self.assertEqual(self.post_forma(eh_global="on", segmentos=[self.produto.pk, self.servico.pk]).status_code, 302)
        f = FormaPagamento.objects.get(nome="Vale Global")
        self.assertTrue(f.eh_global)
        self.assertEqual(set(f.segmentos.all()), {self.produto, self.servico})

    def test_gestor_nao_ve_campos_globais_e_o_valor_enviado_e_ignorado(self):
        self.client.force_login(self.gestor)
        self.assertNotContains(self.client.get(reverse("forma-pagamento-create")), "id_eh_global")
        self.post_forma(eh_global="on", segmentos=[self.produto.pk])
        f = FormaPagamento.objects.get(nome="Vale Global", cliente=self.cliente)
        self.assertFalse(f.eh_global)
        self.assertEqual(f.segmentos.count(), 0)

    def test_cliente_nao_edita_forma_global_e_so_a_ve_no_segmento_liberado(self):
        f = self.forma_global()
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("forma-pagamento-edit", args=[f.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("forma-pagamento-edit", args=[f.pk]), {"nome": "Hack", "tipo": "pix", "taxa_modo": "sem_taxa"}).status_code, 404)
        pagina = self.client.get(reverse("forma-pagamento-list")).content.decode()
        self.assertIn("PIX Global", pagina)
        self.assertNotIn(reverse("forma-pagamento-edit", args=[f.pk]), pagina)  # sem botão de editar
        self.client.force_login(self.gestor_b)  # segmento "servico": não liberado
        self.assertNotIn("PIX Global", self.client.get(reverse("forma-pagamento-list")).content.decode())

    def test_dono_nao_administrador_tambem_nao_altera_item_global(self):
        f = self.forma_global()
        sis_gestor = novo_usuario("gestor_sis", self.sistema, "Gestor")
        self.client.force_login(sis_gestor)
        r = self.client.post(reverse("forma-pagamento-edit", args=[f.pk]), {"nome": "Outro nome", "tipo": "pix", "taxa_modo": "sem_taxa", "ativo": "on"})
        self.assertEqual(r.status_code, 200)  # formulário com erro
        f.refresh_from_db()
        self.assertEqual(f.nome, "PIX Global")

    def test_pdv_aceita_forma_global_so_do_segmento_liberado(self):
        f = self.forma_global()
        caixa = self.abrir()
        venda, _ = self.vender(caixa, qtd=1, pagamentos=[{"forma": f.pk, "valor": 500}])
        self.assertEqual(venda.pagamentos.get().forma_id, f.pk)
        f.segmentos.set([self.servico])  # deixa de ser liberada: uso novo é recusado, a venda feita continua
        with self.assertRaises(RegraNegocioError):
            self.vender(caixa, qtd=1, pagamentos=[{"forma": f.pk, "valor": 500}])
        self.assertEqual(venda.pagamentos.count(), 1)

    def test_api_lista_global_como_somente_leitura_e_bloqueia_escrita(self):
        f = self.forma_global()
        itens = self.api(self.gestor).get(f"{V1}/formas-pagamento/").json()["data"]["itens"]
        global_ = next(i for i in itens if i["id"] == f.pk)
        self.assertEqual((global_["eh_global"], global_["somente_leitura"]), (True, True))
        api = self.api(self.gestor)
        self.assertEqual(api.patch(f"{V1}/formas-pagamento/{f.pk}/", {"nome": "X"}, format="json").status_code, 404)
        self.assertIn(api.delete(f"{V1}/formas-pagamento/{f.pk}/").status_code, (403, 404))  # Gestor nem tem permissão de excluir
        self.assertEqual(self.api(self.gestor_b).get(f"{V1}/formas-pagamento/{f.pk}/").status_code, 404)  # outro segmento
        r = api.post(f"{V1}/formas-pagamento/", {"nome": "Minha", "tipo": "outro", "eh_global": True, "segmentos": [self.produto.pk]}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertFalse(r.json()["data"]["eh_global"])  # não administrador: ignorado
        r = self.api(self.admin).post(f"{V1}/formas-pagamento/", {"nome": "Global API", "tipo": "outro", "eh_global": True, "segmentos": [self.produto.pk]}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["segmentos"]), (201, [self.produto.pk]))


class CanalGlobalTests(GlobaisBase):
    def dados(self, **extra):
        base = {"nome": "Canal G", "descricao": "d", "ativo": "on", "taxas-TOTAL_FORMS": "1", "taxas-INITIAL_FORMS": "0", "taxas-MIN_NUM_FORMS": "0", "taxas-MAX_NUM_FORMS": "1000",
                "taxas-0-descricao": "Comissão", "taxas-0-percentual": "10", "taxas-0-valor_fixo": ""}
        return {**base, **extra}

    def test_admin_cria_canal_global_e_cliente_usa_na_precificacao_sem_editar(self):
        self.assertEqual(self.client.post(reverse("canal-create"), self.dados(eh_global="on")).status_code, 200)  # sem segmento
        self.assertEqual(self.client.post(reverse("canal-create"), self.dados(eh_global="on", segmentos=[self.produto.pk])).status_code, 302)
        canal = CanalVenda.objects.get(nome="Canal G")
        self.assertEqual((canal.cliente_id, canal.eh_global, list(canal.segmentos.all())), (self.sistema.pk, True, [self.produto]))
        # cliente do segmento liberado escolhe o canal na precificação; quem é de outro segmento, não
        self.assertIn(canal, PrecificacaoForm(cliente=self.cliente).fields["canal"].queryset)
        self.assertNotIn(canal, PrecificacaoForm(cliente=self.outro).fields["canal"].queryset)
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("canal-edit", args=[canal.pk])).status_code, 404)
        self.assertIn("Canal G", self.client.get(reverse("canal-list")).content.decode())

    def test_gestor_nao_cria_canal_global(self):
        self.client.force_login(self.gestor)
        self.client.post(reverse("canal-create"), self.dados(eh_global="on", segmentos=[self.produto.pk]))
        canal = CanalVenda.objects.get(nome="Canal G")
        self.assertFalse(canal.eh_global)
        self.assertEqual(canal.cliente_id, self.cliente.pk)

    def test_alterar_taxas_do_global_refaz_precificacoes_de_todos_os_clientes(self):
        canal = self.canal_global()
        self.preco.canal = canal
        self.preco.save()
        antes = self.preco.preco_venda
        from apps.canal.models import CanalTaxa

        salvar_canal(Req(self.admin), canal, [CanalTaxa(descricao="Nova", percentual=Decimal("20"))])
        self.preco.refresh_from_db()
        self.assertGreater(self.preco.preco_venda, antes)

    def test_retirar_segmento_nao_apaga_o_uso_existente(self):
        canal = self.canal_global()
        self.preco.canal = canal
        self.preco.save()
        canal.segmentos.set([self.servico])
        self.preco.refresh_from_db()
        self.assertEqual(self.preco.canal_id, canal.pk)
        self.assertNotIn(canal, PrecificacaoForm(cliente=self.cliente).fields["canal"].queryset.exclude(pk=self.preco.canal_id))

    def test_api_canal_global(self):
        canal = self.canal_global()
        itens = self.api(self.gestor).get(f"{V1}/canais-venda/").json()["data"]["itens"]
        item = next(i for i in itens if i["id"] == canal.pk)
        self.assertEqual((item["eh_global"], item["somente_leitura"]), (True, True))
        self.assertEqual(self.api(self.gestor).patch(f"{V1}/canais-venda/{canal.pk}/", {"nome": "X"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.gestor_b).get(f"{V1}/canais-venda/{canal.pk}/").status_code, 404)
        corpo = {"nome": "Global API", "taxas": [{"descricao": "c", "percentual": "5"}], "eh_global": True, "segmentos": [self.produto.pk]}
        r = self.api(self.admin).post(f"{V1}/canais-venda/", corpo, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["eh_global"], r.json()["data"]["segmentos"]), (201, True, [self.produto.pk]), r.content[:300])
        r = self.api(self.gestor).post(f"{V1}/canais-venda/", {**corpo, "nome": "Meu"}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["eh_global"]), (201, False))


class ServicoNoPdvTests(PdvBase):
    """Serviço (ficha sem insumos, só mão de obra) é precificado e vendido no PDV sem mexer no estoque."""

    def test_servico_so_com_mao_de_obra_vende_sem_baixar_estoque(self):
        from apps.categoria.models import Categoria
        from apps.estoque.models import MovimentoEstoque
        from apps.ficha.models import Ficha, FichaMaoDeObra
        from apps.ficha.services import salvar_ficha
        from apps.maodeobra.models import Colaborador
        from apps.precificacao.models import Precificacao as _P
        METODO_VALOR = _P.METODO_VALOR
        from apps.precificacao.tests import nova_precificacao
        from apps.produto.models import Produto

        self.cliente.segmento = Segmento.objects.get(slug="ambos")
        self.cliente.save()
        colaborador = Colaborador.objects.create(cliente=self.cliente, nome="Ana", valor_hora=3000)
        categoria, _ = Categoria.objects.get_or_create(cliente=self.cliente, nome="Serviços")
        servico = Produto.objects.create(cliente=self.cliente, nome="Corte", categoria=categoria, tipo="servico")
        ficha = Ficha(cliente=self.cliente, produto=servico, nome="Corte")
        salvar_ficha(None, ficha, [], mao_de_obra=[FichaMaoDeObra(colaborador=colaborador, horas=D("0.5"))])
        ficha.refresh_from_db()
        self.assertEqual(ficha.custo_total, 1500)
        preco = nova_precificacao(self.cliente, ficha, metodo_lucro=METODO_VALOR, valor_lucro=2500, percentual_lucro=D("0"), usar_no_pdv=True)
        self.assertEqual(preco.preco_venda, 4000)  # custo de mão de obra + lucro em valor
        caixa = self.abrir()
        movimentos = MovimentoEstoque.objects.count()
        venda, _ = s.registrar_venda(Req(caixa.operador), caixa, [{"produto": servico.pk, "quantidade": 1}], [{"forma": self.dinheiro.pk, "valor": 4000}])
        self.assertEqual(venda.total, 4000)
        self.assertEqual(MovimentoEstoque.objects.count(), movimentos)  # sem insumos: nada a baixar


class CatalogoPdvTests(PdvBase):
    """Catálogo do PDV: nada some em silêncio (limite avisado, API paginada), a busca alcança a categoria e a lista de precificações explica
    por que uma precificação marcada para o PDV não aparece."""

    def mais_produtos(self, quantidade):
        for i in range(quantidade):
            produto = criar_produto(self.cliente, f"Torta {i:02d}")
            ficha = criar_ficha(self.cliente, f"Torta {i:02d}", [(self.farinha, "g", "100")], prod=produto)
            nova_precificacao(self.cliente, ficha, percentual_lucro=D("100"), usar_no_pdv=True)

    def test_tela_avisa_quando_o_catalogo_passa_do_limite(self):
        self.mais_produtos(11)  # + o Bolo da base = 12 produtos no PDV
        caixa = self.abrir()
        self.client.force_login(caixa.operador)
        with mock.patch("apps.pdv.views.LIMITE_CATALOGO", 10):
            dados = self.client.get(reverse("pdv-api-produtos")).json()
        self.assertEqual((len(dados["itens"]), dados["total"], dados["truncado"]), (10, 12, True))
        self.assertEqual(self.client.get(reverse("pdv-api-produtos")).json()["truncado"], False)
        achados = self.client.get(reverse("pdv-api-produtos"), {"q": "Torta 10"}).json()
        self.assertEqual([i["nome"] for i in achados["itens"]], ["Torta 10"])  # o que ficou fora da tela é achado pela busca

    def test_busca_por_categoria(self):
        self.mais_produtos(2)
        self.client.force_login(self.abrir().operador)
        categoria = Precificacao.objects.get(produto__nome="Torta 00").produto.categoria.nome
        self.assertGreaterEqual(len(self.client.get(reverse("pdv-api-produtos"), {"q": categoria[:4]}).json()["itens"]), 3)

    def test_api_do_catalogo_e_paginada_e_compativel(self):
        self.mais_produtos(7)
        caixa = self.abrir(self.operador)
        api = self.api_cliente(self.operador)
        dados = api.get(f"{V1}/pdv/produtos/").json()["data"]
        self.assertEqual((len(dados["itens"]), dados["paginacao"]["total"]), (8, 8))  # tudo em uma página por padrão
        pagina = api.get(f"{V1}/pdv/produtos/", {"por_pagina": 5, "pagina": 2}).json()["data"]
        self.assertEqual((len(pagina["itens"]), pagina["paginacao"]["total_paginas"]), (3, 2))
        self.assertIsNotNone(caixa)

    def test_lista_de_precificacoes_explica_por_que_nao_aparece_no_pdv(self):
        self.ficha.ativo = False
        self.ficha.save()
        self.preco.refresh_from_db()
        self.assertEqual(self.preco.motivo_fora_do_pdv, "A ficha técnica está inativa.")
        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("precificacao")).content.decode()
        self.assertIn("Marcada, mas fora do PDV", pagina)
        self.assertIn("A ficha técnica está inativa.", pagina)
        self.ficha.ativo = True
        self.ficha.save()
        self.bolo.ativo = False
        self.bolo.save()
        self.preco.refresh_from_db()
        self.assertEqual(self.preco.motivo_fora_do_pdv, "O produto está inativo.")
        self.bolo.ativo = True
        self.bolo.save()
        self.preco.refresh_from_db()
        self.assertEqual(self.preco.motivo_fora_do_pdv, "")

    def api_cliente(self, usuario):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")
        return api


class AdministradorSemClienteDoSistemaTests(GlobaisBase):
    """Em produção pode não existir cliente marcado como `sistema`: o administrador global ainda precisa ver e usar os campos globais."""

    def test_campos_globais_aparecem_para_administrador_de_qualquer_unidade(self):
        Cliente.objects.filter(sistema=True).update(sistema=False)
        self.assertContains(self.client.get(reverse("forma-pagamento-create")), "id_eh_global")
        self.assertContains(self.client.get(reverse("canal-create")), "id_eh_global")
        r = self.client.post(reverse("forma-pagamento-create"), {"nome": "Global S/ sistema", "tipo": "outro", "taxa_modo": "sem_taxa", "ativo": "on", "eh_global": "on", "segmentos": [self.produto.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(FormaPagamento.objects.get(nome="Global S/ sistema").eh_global)
        self.client.force_login(self.gestor)  # o cliente do segmento liberado já enxerga
        self.assertContains(self.client.get(reverse("forma-pagamento-list")), "Global S/ sistema")
