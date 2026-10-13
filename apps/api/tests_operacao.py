"""Testes da API v1 dos recursos operacionais: custos comuns, movimentações de estoque (edição),
distribuição do custo operacional por categoria e o novo campo `marca` do insumo."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.categoria.models import Categoria
from apps.custo.models import CustoAjusteCategoria, CustoModelo, CustoOperacional
from apps.estoque.models import Lote, MovimentoEstoque
from apps.estoque.services import registrar_entrada, registrar_saida, saldo_do_insumo
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .tests import V1, BaseAPITests, cliente_api

D = Decimal


class OperacaoBase(BaseAPITests):
    def setUp(self):
        super().setUp()
        for cliente in (self.cliente_a, self.cliente_b):
            for modulo in ("estoque", "custo-operacional", "categoria"):
                habilitar_modulo(cliente, modulo)
        for usuario in (self.usuario_a, self.usuario_b):
            usuario.groups.first().permissions.add(*Permission.objects.filter(content_type__app_label__in=["estoque", "custo"]))
        self.api_a = cliente_api(self.usuario_a)
        self.api_b = cliente_api(self.usuario_b)


class MarcaDoInsumoTests(OperacaoBase):
    def corpo(self, **extra):
        return {"nome": "Leite", "medida": medida("L").pk, "quantidade": "1", "preco_pago": 600, **extra}

    def test_marca_e_opcional_e_aparece_na_resposta(self):
        sem = self.ok(self.api_a.post(f"{V1}/insumos/", self.corpo(), format="json"), 201)
        self.assertEqual(sem["data"]["marca"], "")
        com = self.ok(self.api_a.post(f"{V1}/insumos/", self.corpo(nome="Leite 2", marca="Qualy"), format="json"), 201)
        self.assertEqual(com["data"]["marca"], "Qualy")

    def test_marca_invalida_e_recusada(self):
        for marca in ("<b>x</b>", "x" * 61):
            self.erro(self.api_a.post(f"{V1}/insumos/", self.corpo(marca=marca), format="json"), 400)

    def test_editar_a_marca_audita_antes_e_depois(self):
        from apps.auditoria.models import RegistroAuditoria

        criado = self.ok(self.api_a.post(f"{V1}/insumos/", self.corpo(marca="Qualy"), format="json"), 201)["data"]
        self.ok(self.api_a.patch(f"{V1}/insumos/{criado['id']}/", {"marca": "Piracanjuba"}, format="json"))
        registro = RegistroAuditoria.objects.filter(acao="editar", modulo="insumo").latest("pk")
        self.assertIn({"campo": "marca", "rotulo": "Marca do produto", "antes": "Qualy", "depois": "Piracanjuba"}, registro.alteracoes)


class CustosComunsAPITests(OperacaoBase):
    def criar(self, api=None, **extra):
        return (api or self.api_a).post(f"{V1}/custos-comuns/", {"nome": "Seguro", "tipo": "fixo", "descricao": "Predial", **extra}, format="json")

    def test_crud_basico_com_dono_vindo_do_usuario(self):
        corpo = self.ok(self.criar(cliente=self.cliente_b.pk), 201)["data"]  # tentativa de forjar o dono
        self.assertEqual(corpo["origem"], "proprio")
        self.assertEqual(CustoModelo.objects.get(pk=corpo["id"]).cliente_id, self.cliente_a.pk)
        self.ok(self.api_a.patch(f"{V1}/custos-comuns/{corpo['id']}/", {"descricao": "Novo"}, format="json"))
        self.assertEqual(CustoModelo.objects.get(pk=corpo["id"]).descricao, "Novo")
        self.assertEqual(self.api_a.get(f"{V1}/custos-comuns/{corpo['id']}/").status_code, 200)

    def test_lista_mostra_padroes_e_proprios_mas_nunca_de_outra_empresa(self):
        self.criar()
        self.criar(self.api_b, nome="Alheio")
        nomes = [c["nome"] for c in self.ok(self.api_a.get(f"{V1}/custos-comuns/?por_pagina=100"))["data"]["itens"]]
        self.assertIn("Água", nomes)
        self.assertIn("Seguro", nomes)
        self.assertNotIn("Alheio", nomes)

    def test_filtros(self):
        self.criar()
        def nomes(**p):
            return [c["nome"] for c in self.ok(self.api_a.get(f"{V1}/custos-comuns/", p))["data"]["itens"]]
        self.assertEqual(nomes(origem="proprio"), ["Seguro"])
        self.assertEqual(nomes(origem="sistema", tipo="variavel", q="Energia"), ["Energia elétrica"])

    def test_padrao_do_sistema_e_somente_leitura(self):
        padrao = CustoModelo.objects.get(nome="Água")
        self.erro(self.api_a.patch(f"{V1}/custos-comuns/{padrao.pk}/", {"descricao": "x"}, format="json"), 404)
        self.erro(self.api_a.delete(f"{V1}/custos-comuns/{padrao.pk}/"), 404)

    def test_administrador_edita_e_exclui_o_padrao_do_sistema_e_usuario_comum_nao(self):
        from django.contrib.auth.models import Group, User

        padrao = CustoModelo.objects.get(nome="Água")
        self.erro(self.api_a.patch(f"{V1}/custos-comuns/{padrao.pk}/", {"descricao": "x"}, format="json"), 404)
        adm = User.objects.create_user(username="adm2", email="adm2@teste.local", password="SenhaForte123!")
        adm.groups.add(Group.objects.get(name="Administrador"))
        adm.profile.cliente = self.cliente_a
        adm.profile.save()
        api_adm = cliente_api(adm)
        corpo = self.ok(api_adm.patch(f"{V1}/custos-comuns/{padrao.pk}/", {"descricao": "Conta de água"}, format="json"))["data"]
        self.assertEqual((corpo["origem"], corpo["descricao"]), ("sistema", "Conta de água"))
        self.assertEqual(api_adm.delete(f"{V1}/custos-comuns/{padrao.pk}/").status_code, 204)
        self.assertFalse(CustoModelo.objects.filter(pk=padrao.pk).exists())

    def test_outra_empresa_nao_edita_nem_exclui(self):
        mine = self.ok(self.criar(), 201)["data"]["id"]
        self.erro(self.api_b.patch(f"{V1}/custos-comuns/{mine}/", {"descricao": "x"}, format="json"), 404)
        self.erro(self.api_b.delete(f"{V1}/custos-comuns/{mine}/"), 404)

    def test_nome_repetido_e_conflito(self):
        self.ok(self.criar(), 201)
        self.erro(self.criar(), 409)
        self.erro(self.criar(nome="Água"), 409)  # igual a um padrão do sistema

    def test_excluir_sem_uso_apaga_e_com_uso_desativa(self):
        livre = self.ok(self.criar(), 201)["data"]["id"]
        usado = self.ok(self.criar(nome="Usado"), 201)["data"]["id"]
        CustoOperacional.objects.create(cliente=self.cliente_a, nome="Usado", modelo_id=usado, valor=1)
        self.assertEqual(self.api_a.delete(f"{V1}/custos-comuns/{livre}/").status_code, 204)
        self.assertEqual(self.api_a.delete(f"{V1}/custos-comuns/{usado}/").status_code, 204)
        self.assertFalse(CustoModelo.objects.filter(pk=livre).exists())
        self.assertFalse(CustoModelo.objects.get(pk=usado).ativo)

    def test_permissao_por_acao_e_modulo(self):
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="add_customodelo"))
        api = cliente_api(self.usuario_a)
        self.erro(api.post(f"{V1}/custos-comuns/", {"nome": "X", "tipo": "fixo"}, format="json"), 403)
        self.assertEqual(api.get(f"{V1}/custos-comuns/").status_code, 200)
        self.erro(self.anonimo.get(f"{V1}/custos-comuns/"), 401)


class MovimentosAPITests(OperacaoBase):
    def setUp(self):
        super().setUp()
        self.farinha = novo_insumo(self.cliente_a, "Farinha", "kg", "1", 1000)
        self.entrada = registrar_entrada(self.usuario_a, self.farinha, D("10"), medida("kg"), "L1", timezone.localdate() + timedelta(days=30))
        self.saida = registrar_saida(self.usuario_a, self.farinha, D("4"), medida("kg"))[0]

    def url(self, mov):
        return f"{V1}/estoque/movimentos/{mov.pk}/"

    def test_lista_filtra_e_so_traz_o_proprio_cliente(self):
        outro = novo_insumo(self.cliente_b, "Alheio", "kg", "1", 100)
        registrar_entrada(self.usuario_b, outro, D("1"), medida("kg"))
        corpo = self.ok(self.api_a.get(f"{V1}/estoque/movimentos/", {"tipo": "saida"}))["data"]["itens"]
        self.assertEqual([m["id"] for m in corpo], [self.saida.pk])
        todos = self.ok(self.api_a.get(f"{V1}/estoque/movimentos/"))["data"]["itens"]
        self.assertNotIn("Alheio", [m["insumo_nome"] for m in todos])
        self.assertEqual(corpo[0]["editavel"], True)

    def test_editar_quantidade_recalcula_saldos(self):
        corpo = self.ok(self.api_a.patch(self.url(self.entrada), {"quantidade": "12", "observacao": "conferido"}, format="json"))
        self.assertEqual(corpo["data"]["quantidade"], "12.0000")
        self.assertEqual(saldo_do_insumo(self.farinha), D("8"))
        self.saida.refresh_from_db()
        self.assertEqual(self.saida.saldo_lote_apos, D("8"))

    def test_edicao_que_deixa_saldo_negativo_e_422(self):
        corpo = self.erro(self.api_a.patch(self.url(self.entrada), {"quantidade": "3"}, format="json"), 422)
        self.assertIn("negativo", corpo["message"])
        self.assertEqual(saldo_do_insumo(self.farinha), D("6"))

    def test_put_exige_quantidade_e_unidade_e_convertida(self):
        self.erro(self.api_a.put(self.url(self.saida), {"observacao": "x"}, format="json"), 400)
        self.ok(self.api_a.put(self.url(self.saida), {"quantidade": "500", "medida": medida("g").pk}, format="json"))
        self.assertEqual(saldo_do_insumo(self.farinha), D("9.5"))

    def test_validade_nula_remove_e_omitida_mantem(self):
        sozinho = registrar_entrada(self.usuario_a, self.farinha, D("1"), medida("kg"), "", timezone.localdate() + timedelta(days=5))
        self.ok(self.api_a.patch(self.url(sozinho), {"observacao": "só observação"}, format="json"))
        self.assertIsNotNone(Lote.objects.get(pk=sozinho.lote_id).validade)  # omitida: mantém
        self.ok(self.api_a.patch(self.url(sozinho), {"validade": None}, format="json"))
        self.assertIsNone(Lote.objects.get(pk=sozinho.lote_id).validade)

    def test_outra_empresa_recebe_404(self):
        self.erro(self.api_b.patch(self.url(self.entrada), {"quantidade": "12"}, format="json"), 404)
        self.erro(self.api_b.get(self.url(self.entrada)), 404)
        self.assertEqual(MovimentoEstoque.objects.get(pk=self.entrada.pk).quantidade, D("10"))

    def test_sem_permissao_de_alterar_403_e_sem_criar_nem_apagar(self):
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="change_movimentoestoque"))
        self.erro(cliente_api(self.usuario_a).patch(self.url(self.entrada), {"quantidade": "12"}, format="json"), 403)
        self.erro(self.api_a.post(f"{V1}/estoque/movimentos/", {}, format="json"), 400)  # criação existe; corpo vazio é inválido
        self.erro(self.api_a.delete(self.url(self.entrada)), 405)


class CustoCategoriaAPITests(OperacaoBase):
    def setUp(self):
        super().setUp()
        self.doces = Categoria.objects.create(cliente=self.cliente_a, nome="Doces", quantidade_vendida=100)
        self.bebidas = Categoria.objects.create(cliente=self.cliente_a, nome="Bebidas", quantidade_vendida=100)
        self.salgados = Categoria.objects.create(cliente=self.cliente_a, nome="Salgados", quantidade_vendida=100)
        CustoOperacional.objects.create(cliente=self.cliente_a, nome="Água+luz+gás", valor=30000)

    def test_distribuicao_em_centavos_fecha_com_o_total(self):
        dados = self.ok(self.api_a.get(f"{V1}/custo-operacional/categorias/"))["data"]
        self.assertEqual((dados["total"], dados["distribuido"], dados["nao_distribuido"]), (30000, 30000, 0))
        self.assertEqual([c["valor"] for c in dados["categorias"]], [10000, 10000, 10000])
        self.assertEqual(dados["categorias"][0]["por_unidade"], 100)  # R$ 1,00 por item

    def test_ajuste_manual_redistribui_o_resto_e_restaura(self):
        url = f"{V1}/custo-operacional/categorias/{self.doces.pk}/ajuste/"
        corpo = self.ok(self.api_a.put(url, {"valor": 20000}, format="json"))["data"]
        self.assertEqual((corpo["valor"], corpo["ajuste_manual"], corpo["origem"]), (20000, True, "manual"))
        dados = self.ok(self.api_a.get(f"{V1}/custo-operacional/categorias/"))["data"]
        self.assertEqual(sorted(c["valor"] for c in dados["categorias"]), [5000, 5000, 20000])
        restaurado = self.ok(self.api_a.delete(url))["data"]
        self.assertEqual((restaurado["valor"], restaurado["ajuste_manual"]), (10000, False))
        self.assertFalse(CustoAjusteCategoria.objects.exists())

    def test_ajuste_invalido_e_422_ou_400(self):
        url = f"{V1}/custo-operacional/categorias/{self.doces.pk}/ajuste/"
        self.erro(self.api_a.put(url, {"valor": 30001}, format="json"), 422)  # maior que o total
        self.erro(self.api_a.put(url, {"valor": -1}, format="json"), 400)
        self.erro(self.api_a.put(url, {}, format="json"), 400)

    def test_categoria_de_outra_empresa_404_e_nao_vaza(self):
        self.erro(self.api_b.put(f"{V1}/custo-operacional/categorias/{self.doces.pk}/ajuste/", {"valor": 1}, format="json"), 404)
        dados = self.ok(self.api_b.get(f"{V1}/custo-operacional/categorias/"))["data"]
        self.assertEqual(dados["categorias"], [])

    def test_somente_leitura_nao_ajusta(self):
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="change_custooperacional"))
        api = cliente_api(self.usuario_a)
        self.assertEqual(api.get(f"{V1}/custo-operacional/categorias/").status_code, 200)
        self.erro(api.put(f"{V1}/custo-operacional/categorias/{self.doces.pk}/ajuste/", {"valor": 1}, format="json"), 403)
        self.erro(self.anonimo.get(f"{V1}/custo-operacional/categorias/"), 401)


class SwaggerDosNovosEndpointsTests(OperacaoBase):
    def test_schema_descreve_os_endpoints_novos(self):
        self.client.force_login(self.admin)
        schema = self.client.get("/api/schema/", {"format": "json"}).json()
        caminhos = schema["paths"]
        for esperado in (
            "/api/v1/custos-comuns/", "/api/v1/custos-comuns/{id}/", "/api/v1/estoque/movimentos/", "/api/v1/estoque/movimentos/{id}/",
            "/api/v1/custo-operacional/categorias/", "/api/v1/custo-operacional/categorias/{id}/ajuste/",
        ):
            self.assertIn(esperado, caminhos)
        self.assertIn("put", caminhos["/api/v1/estoque/movimentos/{id}/"])
        self.assertIn("post", caminhos["/api/v1/estoque/movimentos/"])  # criação de movimentos
        self.assertIn("delete", caminhos["/api/v1/custo-operacional/categorias/{id}/ajuste/"])
        self.assertIn("marca", schema["components"]["schemas"]["Insumo"]["properties"])
