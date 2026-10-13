"""Testes da API v1 dos cadastros: produtos, canais de venda, custos operacionais, processados, fichas técnicas
e precificações — validação com os formulários das telas, tenant, permissões, exclusão segura e cálculos."""
from decimal import Decimal

from django.contrib.auth.models import Permission

from apps.auditoria.models import RegistroAuditoria
from apps.categoria.models import Categoria
from apps.custo.models import CustoOperacional
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .tests import V1, BaseAPITests, cliente_api

D = Decimal
MODULOS = ("produto", "categoria", "processado", "ficha", "precificacao", "canal-venda", "custo-operacional", "estoque")
APPS = ["produto", "processado", "ficha", "precificacao", "canal", "custo", "estoque"]


class CadastrosBase(BaseAPITests):
    def setUp(self):
        super().setUp()
        for cliente in (self.cliente_a, self.cliente_b):
            for modulo in MODULOS:
                habilitar_modulo(cliente, modulo)
        for usuario in (self.usuario_a, self.usuario_b):
            usuario.groups.first().permissions.add(*Permission.objects.filter(content_type__app_label__in=APPS))
        self.api_a, self.api_b = cliente_api(self.usuario_a), cliente_api(self.usuario_b)
        self.doces_a = Categoria.objects.create(cliente=self.cliente_a, nome="Doces")
        self.doces_b = Categoria.objects.create(cliente=self.cliente_b, nome="Doces")
        self.farinha_a = novo_insumo(self.cliente_a, "Farinha", "kg", "1", 1000)
        self.farinha_b = novo_insumo(self.cliente_b, "Farinha", "kg", "1", 1000)

    def post(self, caminho, corpo, api=None, codigo=201):
        return self.ok((api or self.api_a).post(f"{V1}/{caminho}/", corpo, format="json"), codigo)["data"]

    def lista(self, caminho, api=None, **params):
        return self.ok((api or self.api_a).get(f"{V1}/{caminho}/", params))["data"]["itens"]

    def produto(self, nome="Bolo", api=None, categoria=None):
        return self.post("produtos", {"nome": nome, "categoria": categoria or self.doces_a.pk}, api)

    def ficha(self, produto=None, api=None, insumo=None, quantidade="1"):
        produto = produto or self.produto()
        return self.post("fichas-tecnicas", {"produto": produto["id"], "itens": [{"insumo": (insumo or self.farinha_a).pk, "medida": medida("kg").pk, "quantidade": quantidade}]}, api)

    def canal(self, nome="IFOOD", api=None):
        return self.post("canais-venda", {"nome": nome, "descricao": "Motoboy", "taxas": [{"descricao": "Comissão", "percentual": "26.20"}, {"descricao": "Antecipação", "percentual": "1.59"}]}, api)


class ProdutosAPITests(CadastrosBase):
    def test_crud_e_dono_vindo_do_usuario(self):
        criado = self.post("produtos", {"nome": "Bolo", "categoria": self.doces_a.pk, "cliente": self.cliente_b.pk})  # forjar o dono
        self.assertEqual((criado["categoria_nome"], criado["ativo"]), ("Doces", True))
        from apps.produto.models import Produto

        self.assertEqual(Produto.objects.get(pk=criado["id"]).cliente_id, self.cliente_a.pk)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="produto", acao="criar").exists())
        atualizado = self.ok(self.api_a.patch(f"{V1}/produtos/{criado['id']}/", {"descricao": "de chocolate"}, format="json"))["data"]
        self.assertEqual((atualizado["nome"], atualizado["descricao"]), ("Bolo", "de chocolate"))  # PATCH mantém o restante
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/produtos/{criado['id']}/"))["data"]["descricao"], "de chocolate")

    def test_validacoes_da_tela_valem_na_api(self):
        self.produto()
        self.erro(self.api_a.post(f"{V1}/produtos/", {"nome": "bolo", "categoria": self.doces_a.pk}, format="json"), 400)  # nome repetido
        self.erro(self.api_a.post(f"{V1}/produtos/", {"nome": "X", "categoria": self.doces_b.pk}, format="json"), 400)  # categoria de outra empresa
        self.erro(self.api_a.post(f"{V1}/produtos/", {"nome": "", "categoria": self.doces_a.pk}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/produtos/", {"categoria": self.doces_a.pk}, format="json"), 400)

    def test_lista_filtros_e_isolamento(self):
        self.produto("Bolo")
        self.produto("Torta")
        self.produto("Alheio", api=self.api_b, categoria=self.doces_b.pk)
        self.assertEqual([p["nome"] for p in self.lista("produtos")], ["Bolo", "Torta"])
        self.assertEqual([p["nome"] for p in self.lista("produtos", q="tor")], ["Torta"])
        self.assertEqual(len(self.lista("produtos", categoria=self.doces_a.pk)), 2)
        self.assertEqual([p["nome"] for p in self.lista("produtos", ordenar="-nome")], ["Torta", "Bolo"])

    def test_excluir_sem_uso_apaga_e_com_ficha_so_desativa(self):
        livre = self.produto("Livre")
        usado = self.produto("Usado")
        self.ficha(produto=usado)
        self.assertEqual(self.api_a.delete(f"{V1}/produtos/{livre['id']}/").status_code, 204)
        self.assertEqual(self.api_a.delete(f"{V1}/produtos/{usado['id']}/").status_code, 204)
        self.assertEqual([p["nome"] for p in self.lista("produtos", ativo="todos")], ["Usado"])
        self.assertFalse(self.lista("produtos", ativo="todos")[0]["ativo"])

    def test_outra_empresa_permissao_e_autenticacao(self):
        p = self.produto()
        self.erro(self.api_b.get(f"{V1}/produtos/{p['id']}/"), 404)
        self.erro(self.api_b.patch(f"{V1}/produtos/{p['id']}/", {"nome": "x"}, format="json"), 404)
        self.erro(self.api_b.delete(f"{V1}/produtos/{p['id']}/"), 404)
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="add_produto"))
        self.erro(cliente_api(self.usuario_a).post(f"{V1}/produtos/", {"nome": "Z", "categoria": self.doces_a.pk}, format="json"), 403)
        self.erro(self.anonimo.get(f"{V1}/produtos/"), 401)


class CanaisAPITests(CadastrosBase):
    def test_cria_com_taxas_e_calcula_totais(self):
        canal = self.canal()
        self.assertEqual((canal["taxa_percentual_total"], canal["liquido_percentual"]), ("27.7900", "72.2100"))
        self.assertEqual([t["descricao"] for t in canal["taxas"]], ["Comissão", "Antecipação"])

    def test_taxa_com_percentual_e_valor_fixo_juntos_e_recusada(self):
        self.erro(self.api_a.post(f"{V1}/canais-venda/", {"nome": "X", "taxas": [{"percentual": "5", "valor_fixo": 100}]}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/canais-venda/", {"nome": "X", "taxas": [{"descricao": "vazia"}]}, format="json"), 400)

    def test_patch_sem_taxas_mantem_e_put_com_taxas_substitui(self):
        canal = self.canal()
        self.ok(self.api_a.patch(f"{V1}/canais-venda/{canal['id']}/", {"descricao": "Nova"}, format="json"))
        self.assertEqual(len(self.ok(self.api_a.get(f"{V1}/canais-venda/{canal['id']}/"))["data"]["taxas"]), 2)
        novo = self.ok(self.api_a.put(f"{V1}/canais-venda/{canal['id']}/", {"nome": "IFOOD", "taxas": [{"valor_fixo": 150}]}, format="json"))["data"]
        self.assertEqual((novo["taxa_percentual_total"], novo["taxa_fixa_total"]), ("0.0000", 150))

    def test_taxas_que_inviabilizam_precificacao_sao_recusadas(self):
        canal = self.canal()
        ficha = self.ficha()
        self.post("precificacoes", {"ficha": ficha["id"], "canal": canal["id"], "metodo_lucro": "margem", "percentual_lucro": "70"})
        corpo = {"nome": "IFOOD", "taxas": [{"percentual": "40"}]}  # 40% + 70% de margem chega a 110%
        self.erro(self.api_a.put(f"{V1}/canais-venda/{canal['id']}/", corpo, format="json"), 400)
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/canais-venda/{canal['id']}/"))["data"]["taxa_percentual_total"], "27.7900")  # nada mudou

    def test_excluir_sem_uso_e_em_uso_e_isolamento(self):
        livre, usado = self.canal("Livre"), self.canal("Usado")
        self.post("precificacoes", {"ficha": self.ficha()["id"], "canal": usado["id"], "metodo_lucro": "markup", "percentual_lucro": "50"})
        self.assertEqual(self.api_a.delete(f"{V1}/canais-venda/{livre['id']}/").status_code, 204)
        self.assertEqual(self.api_a.delete(f"{V1}/canais-venda/{usado['id']}/").status_code, 204)
        restante = self.lista("canais-venda", ativo="todos")
        self.assertEqual([(c["nome"], c["ativo"]) for c in restante], [("Usado", False)])
        self.erro(self.api_b.get(f"{V1}/canais-venda/{usado['id']}/"), 404)


class CustosAPITests(CadastrosBase):
    def test_cria_edita_e_apaga_custo(self):
        criado = self.post("custos-operacionais", {"nome": "Aluguel", "tipo": "fixo", "valor": 35000})
        self.assertEqual((criado["valor"], criado["valor_formatado"]), (35000, "R$ 350,00"))
        editado = self.ok(self.api_a.patch(f"{V1}/custos-operacionais/{criado['id']}/", {"valor": 40000}, format="json"))["data"]
        self.assertEqual(editado["valor_formatado"], "R$ 400,00")
        self.assertEqual(self.api_a.delete(f"{V1}/custos-operacionais/{criado['id']}/").status_code, 204)
        self.assertFalse(CustoOperacional.objects.exists())

    def test_custo_comum_define_tipo_e_nome(self):
        from apps.custo.models import CustoModelo

        agua = CustoModelo.objects.get(nome="Água")
        criado = self.post("custos-operacionais", {"modelo": agua.pk, "valor": 9000})
        self.assertEqual((criado["nome"], criado["tipo"], criado["modelo"]), ("Água", "variavel", agua.pk))
        self.erro(self.api_a.post(f"{V1}/custos-operacionais/", {"modelo": agua.pk, "valor": 1}, format="json"), 400)  # já usado

    def test_validacoes_e_filtros(self):
        self.post("custos-operacionais", {"nome": "Aluguel", "tipo": "fixo", "valor": 100})
        self.post("custos-operacionais", {"nome": "Energia", "tipo": "variavel", "valor": 200})
        self.erro(self.api_a.post(f"{V1}/custos-operacionais/", {"nome": "aluguel", "valor": 1}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/custos-operacionais/", {"nome": "X", "valor": -5}, format="json"), 400)
        self.assertEqual([c["nome"] for c in self.lista("custos-operacionais", tipo="variavel")], ["Energia"])
        self.assertEqual([c["nome"] for c in self.lista("custos-operacionais", ordenar="-valor")], ["Energia", "Aluguel"])

    def test_distribuicao_acompanha_o_custo_criado(self):
        Categoria.objects.filter(pk=self.doces_a.pk).update(quantidade_vendida=100)
        self.post("custos-operacionais", {"nome": "Água+luz+gás", "tipo": "fixo", "valor": 30000})
        dados = self.ok(self.api_a.get(f"{V1}/custo-operacional/categorias/"))["data"]
        self.assertEqual((dados["total"], dados["categorias"][0]["valor"]), (30000, 30000))

    def test_outra_empresa_nao_ve_nem_altera(self):
        c = self.post("custos-operacionais", {"nome": "Aluguel", "tipo": "fixo", "valor": 100})
        self.erro(self.api_b.patch(f"{V1}/custos-operacionais/{c['id']}/", {"valor": 1}, format="json"), 404)
        self.assertEqual(self.lista("custos-operacionais", api=self.api_b), [])


class ProcessadosAPITests(CadastrosBase):
    def corpo(self, **extra):
        base = {
            "nome": "Camarão limpo", "rendimento_quantidade": "250", "rendimento_medida": medida("g").pk,
            "itens": [{"insumo": self.camarao.pk, "medida": medida("g").pk, "quantidade": "500"}],
        }
        return {**base, **extra}

    def setUp(self):
        super().setUp()
        self.camarao = novo_insumo(self.cliente_a, "Camarão", "kg", "1", 3990)

    def test_cria_calcula_custo_e_gera_insumo(self):
        p = self.post("processados", self.corpo())
        self.assertEqual((p["custo_total"], p["custo_total_formatado"]), (1995, "R$ 19,95"))  # 0,5 kg × R$ 39,90
        self.assertIsNotNone(p["insumo_gerado"])
        self.assertEqual(p["itens"][0]["insumo_nome"], "Camarão")
        gerado = self.ok(self.api_a.get(f"{V1}/insumos/{p['insumo_gerado']}/"))["data"]
        self.assertEqual((gerado["quantidade"], gerado["preco_pago"]), ("1.0000", 7980))  # 1 kg ao custo do quilo

    def test_patch_sem_itens_mantem_a_ficha_e_put_substitui(self):
        p = self.post("processados", self.corpo())
        mesmo = self.ok(self.api_a.patch(f"{V1}/processados/{p['id']}/", {"nome": "Camarão limpo 2"}, format="json"))["data"]
        self.assertEqual((mesmo["nome"], len(mesmo["itens"]), mesmo["custo_total"]), ("Camarão limpo 2", 1, 1995))
        novo = self.ok(self.api_a.put(f"{V1}/processados/{p['id']}/", self.corpo(nome="Camarão limpo 2", itens=[{"insumo": self.camarao.pk, "medida": medida("kg").pk, "quantidade": "1"}]), format="json"))["data"]
        self.assertEqual(novo["custo_total"], 3990)

    def test_validacoes(self):
        self.erro(self.api_a.post(f"{V1}/processados/", self.corpo(itens=[]), format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/processados/", self.corpo(itens=[{"insumo": self.farinha_b.pk, "medida": medida("kg").pk, "quantidade": "1"}]), format="json"), 400)  # insumo alheio
        dupla = [{"insumo": self.camarao.pk, "medida": medida("g").pk, "quantidade": "1"}] * 2
        self.erro(self.api_a.post(f"{V1}/processados/", self.corpo(itens=dupla), format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/processados/", self.corpo(itens=[{"insumo": self.camarao.pk, "medida": medida("g").pk, "quantidade": "0"}]), format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/processados/", self.corpo(nome="Camarão"), format="json"), 400)  # colide com um insumo

    def test_excluir_e_logico_e_inativa_o_insumo_gerado(self):
        p = self.post("processados", self.corpo())
        self.assertEqual(self.api_a.delete(f"{V1}/processados/{p['id']}/").status_code, 204)
        self.assertFalse(self.lista("processados", ativo="todos")[0]["ativo"])
        from apps.insumo.models import Insumo

        self.assertFalse(Insumo.objects.get(pk=p["insumo_gerado"]).ativo)

    def test_isolamento_e_permissao(self):
        p = self.post("processados", self.corpo())
        self.erro(self.api_b.get(f"{V1}/processados/{p['id']}/"), 404)
        self.erro(self.api_b.delete(f"{V1}/processados/{p['id']}/"), 404)
        self.assertEqual(self.lista("processados", api=self.api_b), [])
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="change_processado"))
        self.erro(cliente_api(self.usuario_a).patch(f"{V1}/processados/{p['id']}/", {"nome": "x"}, format="json"), 403)


class FichasAPITests(CadastrosBase):
    def test_nome_vem_do_produto_e_custo_e_calculado(self):
        produto = self.produto("Pizza")
        f = self.post("fichas-tecnicas", {"produto": produto["id"], "nome": "Hackeado", "itens": [{"insumo": self.farinha_a.pk, "medida": medida("g").pk, "quantidade": "250"}]})
        self.assertEqual((f["nome"], f["custo_total"], f["produto_nome"]), ("Pizza", 250, "Pizza"))
        outra = self.ficha(produto=produto)
        self.assertEqual(outra["nome"], "Pizza (2)")

    def test_edicao_recalcula_e_validacoes(self):
        f = self.ficha()
        novo = self.ok(self.api_a.patch(f"{V1}/fichas-tecnicas/{f['id']}/", {"itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "3"}]}, format="json"))["data"]
        self.assertEqual(novo["custo_total"], 3000)
        self.erro(self.api_a.post(f"{V1}/fichas-tecnicas/", {"produto": 999999, "itens": []}, format="json"), 400)
        produto_b = self.produto("Alheio", api=self.api_b, categoria=self.doces_b.pk)
        self.erro(self.api_a.post(f"{V1}/fichas-tecnicas/", {"produto": produto_b["id"], "itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "1"}]}, format="json"), 400)

    def test_recalculo_quando_o_insumo_muda(self):
        f = self.ficha()
        self.ok(self.api_a.patch(f"{V1}/insumos/{self.farinha_a.pk}/", {"preco_pago": 2000}, format="json"))
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/fichas-tecnicas/{f['id']}/"))["data"]["custo_total"], 2000)

    def test_excluir_com_precificacao_desativa_e_isolamento(self):
        f = self.ficha()
        self.post("precificacoes", {"ficha": f["id"], "canal_nome": "Balcão", "metodo_lucro": "valor", "valor_lucro": 300})
        self.assertEqual(self.api_a.delete(f"{V1}/fichas-tecnicas/{f['id']}/").status_code, 204)
        self.assertFalse(self.lista("fichas-tecnicas", ativo="todos")[0]["ativo"])
        self.erro(self.api_b.get(f"{V1}/fichas-tecnicas/{f['id']}/"), 404)


class PrecificacoesAPITests(CadastrosBase):
    def setUp(self):
        super().setUp()
        self.f = self.ficha()  # custo R$ 10,00
        self.canal_ifood = self.canal()

    def corpo(self, **extra):
        return {"ficha": self.f["id"], "canal": self.canal_ifood["id"], "metodo_lucro": "margem", "percentual_lucro": "20", **extra}

    def test_calcula_com_a_formula_das_telas(self):
        p = self.post("precificacoes", self.corpo(custo_operacional=200))
        # custo 1000 + operacional 200 = 1200; preço = 1200 ÷ (1 − 0,2779 − 0,20)
        self.assertEqual((p["custo_ficha"], p["custo_total"], p["canal_nome"]), (1000, 1200, "IFOOD"))
        self.assertAlmostEqual(p["lucro"] / p["preco_venda"], 0.20, delta=0.001)
        self.assertTrue(p["custo_operacional_manual"])
        self.assertEqual(p["preco_venda_formatado"], f"R$ {p['preco_venda'] // 100},{p['preco_venda'] % 100:02d}".replace("R$ 1", "R$ 1"))

    def test_custo_operacional_omitido_e_automatico_e_acompanha_a_distribuicao(self):
        Categoria.objects.filter(pk=self.doces_a.pk).update(quantidade_vendida=100)
        CustoOperacional.objects.create(cliente=self.cliente_a, nome="Água+luz+gás", valor=30000)  # R$ 3,00 por item
        p = self.post("precificacoes", self.corpo())
        self.assertEqual((p["custo_operacional"], p["custo_operacional_manual"]), (300, False))
        CustoOperacional.objects.filter(cliente=self.cliente_a).update(valor=50000)
        self.post("custos-operacionais", {"nome": "Outro", "tipo": "fixo", "valor": 0})  # qualquer salvar de custo refaz as automáticas
        atual = self.ok(self.api_a.get(f"{V1}/precificacoes/{p['id']}/"))["data"]
        self.assertEqual(atual["custo_operacional"], 500)

    def test_canal_personalizado_e_validacoes(self):
        p = self.post("precificacoes", {"ficha": self.f["id"], "canal_nome": "Balcão", "taxa_percentual": "5", "taxa_fixa": 100, "metodo_lucro": "valor", "valor_lucro": 300})
        self.assertEqual((p["canal"], p["preco_venda"]), (None, 1474))  # (1000 + 300 + 100) ÷ 0,95
        self.erro(self.api_a.post(f"{V1}/precificacoes/", {"ficha": self.f["id"], "metodo_lucro": "margem", "percentual_lucro": "20"}, format="json"), 400)  # sem canal
        self.erro(self.api_a.post(f"{V1}/precificacoes/", self.corpo(percentual_lucro="75"), format="json"), 400)  # margem + taxas ≥ 100%
        self.erro(self.api_a.post(f"{V1}/precificacoes/", self.corpo(metodo_lucro="inexistente"), format="json"), 400)

    def test_patch_recalcula_e_mantem_o_resto(self):
        p = self.post("precificacoes", self.corpo())
        novo = self.ok(self.api_a.patch(f"{V1}/precificacoes/{p['id']}/", {"metodo_lucro": "markup", "percentual_lucro": "100"}, format="json"))["data"]
        self.assertEqual((novo["canal"], novo["metodo_lucro"]), (self.canal_ifood["id"], "markup"))
        self.assertGreater(novo["preco_venda"], p["preco_venda"])

    def test_filtros_exclusao_e_isolamento(self):
        a = self.post("precificacoes", self.corpo())
        self.post("precificacoes", {"ficha": self.f["id"], "canal_nome": "Balcão", "metodo_lucro": "valor", "valor_lucro": 300})
        self.assertEqual(len(self.lista("precificacoes", metodo="valor")), 1)
        self.assertEqual(len(self.lista("precificacoes", canal=self.canal_ifood["id"])), 1)
        self.assertEqual(self.api_a.delete(f"{V1}/precificacoes/{a['id']}/").status_code, 204)
        self.assertEqual(len(self.lista("precificacoes", ativo="todos")), 1)
        self.erro(self.api_b.get(f"{V1}/precificacoes/{a['id']}/"), 404)
        ficha_b = self.ficha(api=self.api_b, produto=self.produto("PB", api=self.api_b, categoria=self.doces_b.pk), insumo=self.farinha_b)
        self.erro(self.api_a.post(f"{V1}/precificacoes/", {"ficha": ficha_b["id"], "canal_nome": "X", "metodo_lucro": "valor", "valor_lucro": 1}, format="json"), 400)


class SwaggerDosCadastrosTests(CadastrosBase):
    def test_schema_descreve_os_recursos(self):
        self.client.force_login(self.admin)
        caminhos = self.client.get("/api/schema/", {"format": "json"}).json()["paths"]
        for recurso in ("produtos", "canais-venda", "custos-operacionais", "processados", "fichas-tecnicas", "precificacoes"):
            self.assertIn(f"/api/v1/{recurso}/", caminhos)
            self.assertIn(f"/api/v1/{recurso}/{{id}}/", caminhos)
            self.assertEqual(set(caminhos[f"/api/v1/{recurso}/"]) & {"get", "post"}, {"get", "post"})
            self.assertTrue({"get", "put", "patch", "delete"} <= set(caminhos[f"/api/v1/{recurso}/{{id}}/"]))
