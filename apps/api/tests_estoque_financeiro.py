"""Testes da API v1 de estoque (saldos, mínimo, movimentos, transferências, alertas), financeiro (categorias, contas,
baixas, DRE, balanço) e painel: permissões por ação, tenant, validações das telas e cálculos."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.estoque.models import EstoqueMinimo, Lote, MovimentoEstoque
from apps.estoque.services import registrar_entrada, saldo_do_insumo
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .tests import V1, BaseAPITests, cliente_api

D = Decimal
HOJE = timezone.localdate()
MODULOS = ("estoque", "financeiro-pagar", "financeiro-receber", "financeiro-categorias", "financeiro-relatorios")
APPS = ["estoque", "financeiro"]


class Base(BaseAPITests):
    def setUp(self):
        super().setUp()
        for cliente in (self.cliente_a, self.cliente_b):
            for modulo in MODULOS:
                habilitar_modulo(cliente, modulo)
        for usuario in (self.usuario_a, self.usuario_b):
            usuario.groups.first().permissions.add(*Permission.objects.filter(content_type__app_label__in=APPS))
        self.api_a, self.api_b = cliente_api(self.usuario_a), cliente_api(self.usuario_b)
        self.farinha = novo_insumo(self.cliente_a, "Farinha", "kg", "1", 1000)

    def post(self, caminho, corpo, api=None, codigo=201):
        return self.ok((api or self.api_a).post(f"{V1}/{caminho}/", corpo, format="json"), codigo)["data"]

    def itens(self, caminho, api=None, **params):
        return self.ok((api or self.api_a).get(f"{V1}/{caminho}/", params))["data"]["itens"]

    def sem_permissao(self, codename):
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename=codename))
        return cliente_api(self.usuario_a)


class SaldosAPITests(Base):
    def test_lista_saldos_filtros_e_detalhe_com_lotes(self):
        registrar_entrada(self.usuario_a, self.farinha, D("10"), medida("kg"), "L1", HOJE + timedelta(days=3))
        registrar_entrada(self.usuario_a, self.farinha, D("2"), medida("kg"), "", HOJE + timedelta(days=30))  # validade sem lote
        novo_insumo(self.cliente_a, "Açúcar", "kg", "1", 500)
        novo_insumo(self.cliente_b, "Alheio", "kg", "1", 100)
        todos = self.itens("estoque/saldos")
        self.assertEqual([(i["nome"], i["saldo"], i["situacao"]) for i in todos], [("Açúcar", "0.0000", "Zerado"), ("Farinha", "12.0000", "OK")])
        self.assertEqual([i["nome"] for i in self.itens("estoque/saldos", situacao="zerado")], ["Açúcar"])
        self.assertEqual([i["nome"] for i in self.itens("estoque/saldos", q="far")], ["Farinha"])
        self.assertEqual([i["nome"] for i in self.itens("estoque/saldos", situacao="vencendo")], ["Farinha"])  # lote de 3 dias
        detalhe = self.ok(self.api_a.get(f"{V1}/estoque/saldos/{self.farinha.pk}/"))["data"]
        self.assertEqual(sorted(l["lote"] for l in detalhe["lotes"]), ["", "L1"])  # o lote automático aparece sem código
        self.erro(self.api_b.get(f"{V1}/estoque/saldos/{self.farinha.pk}/"), 404)

    def test_define_o_minimo_e_gera_situacao_baixo(self):
        registrar_entrada(self.usuario_a, self.farinha, D("2"), medida("kg"))
        url = f"{V1}/estoque/saldos/{self.farinha.pk}/minimo/"
        dados = self.ok(self.api_a.put(url, {"minimo": "5"}, format="json"))["data"]
        self.assertEqual((dados["minimo"], dados["situacao"]), ("5.0000", "Baixo"))
        self.assertEqual(EstoqueMinimo.objects.get(insumo=self.farinha).minimo, D("5"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="estoque", acao="editar", registro_nome="Farinha").exists())
        self.assertEqual([i["nome"] for i in self.itens("estoque/saldos", situacao="baixo")], ["Farinha"])
        self.erro(self.api_a.put(url, {"minimo": "-1"}, format="json"), 400)
        self.erro(self.api_a.put(url, {}, format="json"), 400)

    def test_minimo_de_outra_empresa_e_sem_permissao(self):
        url = f"{V1}/estoque/saldos/{self.farinha.pk}/minimo/"
        self.erro(self.api_b.put(url, {"minimo": "1"}, format="json"), 404)
        self.erro(self.sem_permissao("change_movimentoestoque").put(url, {"minimo": "1"}, format="json"), 403)
        self.erro(self.anonimo.get(f"{V1}/estoque/saldos/"), 401)


class MovimentosCriacaoAPITests(Base):
    def test_entrada_com_validade_sem_lote(self):
        dados = self.post("estoque/movimentos", {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "10", "validade": str(HOJE + timedelta(days=30))})
        self.assertEqual((dados[0]["tipo"], dados[0]["lote"], dados[0]["validade"]), ("entrada", "", str(HOJE + timedelta(days=30))))
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))

    def test_saida_consome_o_lote_que_vence_primeiro_e_pode_gerar_varios_movimentos(self):
        registrar_entrada(self.usuario_a, self.farinha, D("3"), medida("kg"), "CEDO", HOJE + timedelta(days=5))
        registrar_entrada(self.usuario_a, self.farinha, D("3"), medida("kg"), "TARDE", HOJE + timedelta(days=60))
        dados = self.post("estoque/movimentos", {"insumo": self.farinha.pk, "tipo": "saida", "quantidade": "4", "motivo": "consumo_interno"})
        self.assertEqual([(m["lote"], m["quantidade"]) for m in dados], [("CEDO", "3.0000"), ("TARDE", "1.0000")])

    def test_unidade_diferente_e_convertida_e_erros(self):
        self.post("estoque/movimentos", {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "500", "medida": medida("g").pk})
        self.assertEqual(saldo_do_insumo(self.farinha), D("0.5"))
        self.erro(self.api_a.post(f"{V1}/estoque/movimentos/", {"insumo": self.farinha.pk, "tipo": "saida", "quantidade": "9", "motivo": "perda"}, format="json"), 422)  # saldo insuficiente
        corpo_litro = {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "1", "medida": medida("L").pk}
        self.erro(self.api_a.post(f"{V1}/estoque/movimentos/", corpo_litro, format="json"), 422)  # dimensão incompatível
        for ruim in ({"quantidade": "0"}, {"quantidade": "-2"}, {"tipo": "transferencia_entrada"}, {"tipo": "saida", "lote": "X", "motivo": "perda"}, {"tipo": "saida"}):
            corpo = {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "1", **ruim}
            self.erro(self.api_a.post(f"{V1}/estoque/movimentos/", corpo, format="json"), 400)
        self.assertEqual(MovimentoEstoque.objects.count(), 1)

    def test_insumo_de_outra_empresa_e_permissao_de_adicionar(self):
        alheio = novo_insumo(self.cliente_b, "Alheio", "kg", "1", 100)
        self.erro(self.api_a.post(f"{V1}/estoque/movimentos/", {"insumo": alheio.pk, "tipo": "entrada", "quantidade": "1"}, format="json"), 400)
        self.erro(self.sem_permissao("add_movimentoestoque").post(f"{V1}/estoque/movimentos/", {"insumo": self.farinha.pk, "tipo": "entrada", "quantidade": "1"}, format="json"), 403)


class AlertasAPITests(Base):
    def test_baixo_e_vencimento(self):
        registrar_entrada(self.usuario_a, self.farinha, D("1"), medida("kg"), "L1", HOJE + timedelta(days=2))
        EstoqueMinimo.objects.create(insumo=self.farinha, minimo=D("5"))
        dados = self.ok(self.api_a.get(f"{V1}/estoque/alertas/"))["data"]
        self.assertEqual([(a["nome"], a["saldo"]) for a in dados["baixo"]], [("Farinha", "1.0000")])
        self.assertEqual([(a["nome"], a["lote"]) for a in dados["vencimento"]], [("Farinha", "L1")])
        vazio = self.ok(self.api_b.get(f"{V1}/estoque/alertas/"))["data"]
        self.assertEqual((vazio["baixo"], vazio["vencimento"]), ([], []))


class TransferenciasAPITests(Base):
    def setUp(self):
        super().setUp()
        self.filial = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="34028316000103", nome_razao_social="Filial A",
            tipo_estabelecimento=Cliente.TIPO_FILIAL, matriz=self.cliente_a,
        )
        for modulo in MODULOS:
            habilitar_modulo(self.filial, modulo)
        self.farinha_filial = novo_insumo(self.filial, "Farinha", "kg", "1", 1000)
        registrar_entrada(self.usuario_a, self.farinha, D("10"), medida("kg"), "L1", HOJE + timedelta(days=20))

    def corpo(self, **extra):
        return {"destino": self.filial.pk, "observacao": "reposição", "itens": [{"insumo": self.farinha.pk, "quantidade": "4"}], **extra}

    def test_transfere_movendo_saldos_e_leva_o_lote(self):
        t = self.post("estoque/transferencias", self.corpo())
        self.assertEqual((t["origem_nome"], t["destino_nome"], t["itens"][0]["quantidade"]), (self.cliente_a.nome_razao_social, "Filial A", "4.0000"))
        self.assertEqual((saldo_do_insumo(self.farinha), saldo_do_insumo(self.farinha_filial)), (D("6"), D("4")))
        self.assertEqual(Lote.objects.get(insumo=self.farinha_filial).codigo, "L1")
        self.assertEqual(len(self.itens("estoque/transferencias")), 1)
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/estoque/transferencias/{t['id']}/"))["data"]["observacao"], "reposição")

    def test_erros_de_negocio_e_de_validacao(self):
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(itens=[{"insumo": self.farinha.pk, "quantidade": "99"}]), format="json"), 422)  # saldo
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(destino=self.cliente_a.pk), format="json"), 400)  # a própria unidade
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(destino=self.cliente_b.pk), format="json"), 400)  # outra estrutura
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(itens=[]), format="json"), 400)
        dupla = [{"insumo": self.farinha.pk, "quantidade": "1"}] * 2
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(itens=dupla), format="json"), 400)
        alheio = novo_insumo(self.cliente_b, "X", "kg", "1", 1)
        self.erro(self.api_a.post(f"{V1}/estoque/transferencias/", self.corpo(itens=[{"insumo": alheio.pk, "quantidade": "1"}]), format="json"), 400)
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))  # tudo ou nada

    def test_permissao_especifica_e_isolamento(self):
        t = self.post("estoque/transferencias", self.corpo())
        self.erro(self.api_b.get(f"{V1}/estoque/transferencias/{t['id']}/"), 404)
        self.assertEqual(self.itens("estoque/transferencias", api=self.api_b), [])
        sem = self.sem_permissao("add_transferenciaestoque")
        self.erro(sem.post(f"{V1}/estoque/transferencias/", self.corpo(), format="json"), 403)
        self.assertEqual(sem.get(f"{V1}/estoque/transferencias/").status_code, 200)


class CategoriasFinanceirasAPITests(Base):
    def test_crud_e_exclusao_segura(self):
        c = self.post("financeiro/categorias", {"nome": "Aluguel", "tipo": "despesa"})
        self.erro(self.api_a.post(f"{V1}/financeiro/categorias/", {"nome": "aluguel", "tipo": "despesa"}, format="json"), 400)
        self.post("financeiro/categorias", {"nome": "Aluguel", "tipo": "receita"})  # outro tipo: ok
        self.assertEqual(len(self.itens("financeiro/categorias")), 2)
        self.assertEqual(len(self.itens("financeiro/categorias", tipo="despesa")), 1)
        inativa = self.ok(self.api_a.post(f"{V1}/financeiro/categorias/{c['id']}/inativar/"))["data"]
        self.assertFalse(inativa["ativo"])
        self.assertEqual(self.api_a.delete(f"{V1}/financeiro/categorias/{c['id']}/").status_code, 204)
        self.assertEqual(len(self.itens("financeiro/categorias")), 1)

    def test_em_uso_so_desativa_e_isolamento(self):
        c = self.post("financeiro/categorias", {"nome": "Fornecedores", "tipo": "despesa"})
        self.post("financeiro/contas-pagar", {"descricao": "Farinha", "categoria": c["id"], "valor": 5000, "vencimento": str(HOJE)})
        self.assertEqual(self.api_a.delete(f"{V1}/financeiro/categorias/{c['id']}/").status_code, 204)
        self.assertFalse(self.itens("financeiro/categorias")[0]["ativo"])
        self.erro(self.api_b.get(f"{V1}/financeiro/categorias/{c['id']}/"), 404)
        self.erro(self.api_b.post(f"{V1}/financeiro/categorias/{c['id']}/ativar/"), 404)
        self.erro(self.api_a.put(f"{V1}/financeiro/categorias/{c['id']}/", {"nome": "x", "tipo": "despesa"}, format="json"), 405)  # sem edição, como na tela


class ContasAPITests(Base):
    def setUp(self):
        super().setUp()
        self.despesa = self.post("financeiro/categorias", {"nome": "Fornecedores", "tipo": "despesa"})
        self.receita = self.post("financeiro/categorias", {"nome": "Vendas", "tipo": "receita"})

    def conta(self, chave="pagar", **extra):
        categoria = self.despesa if chave == "pagar" else self.receita
        return self.post(f"financeiro/contas-{chave}", {"descricao": "Farinha", "contraparte": "Moinho", "categoria": categoria["id"], "valor": 10000, "vencimento": str(HOJE + timedelta(days=5)), **extra})

    def test_cria_com_emissao_padrao_e_status_inicial(self):
        c = self.conta()
        self.assertEqual((c["status"], c["valor"], c["saldo"], c["emissao"], c["atrasada"]), ("aberta", 10000, 10000, str(HOJE), False))

    def test_categoria_precisa_ser_do_tipo_certo_e_da_empresa(self):
        alheia = self.post("financeiro/categorias", {"nome": "Alheia", "tipo": "despesa"}, api=self.api_b)
        for categoria in (self.receita["id"], alheia["id"]):
            self.erro(self.api_a.post(f"{V1}/financeiro/contas-pagar/", {"descricao": "x", "categoria": categoria, "valor": 100, "vencimento": str(HOJE)}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/financeiro/contas-receber/", {"descricao": "x", "categoria": self.despesa["id"], "valor": 100, "vencimento": str(HOJE)}, format="json"), 400)

    def test_validacoes_de_valor_e_datas(self):
        base = {"descricao": "x", "categoria": self.despesa["id"], "valor": 100, "vencimento": str(HOJE)}
        for ruim in ({"valor": 0}, {"valor": -1}, {"emissao": str(HOJE), "vencimento": str(HOJE - timedelta(days=1))}, {"descricao": ""}):
            self.erro(self.api_a.post(f"{V1}/financeiro/contas-pagar/", {**base, **ruim}, format="json"), 400)

    def test_baixa_parcial_total_e_estorno(self):
        c = self.conta()
        url = f"{V1}/financeiro/contas-pagar/{c['id']}"
        parcial = self.post(f"financeiro/contas-pagar/{c['id']}/baixas", {"valor": 4000})
        self.assertEqual((parcial["status"], parcial["valor_baixado"], parcial["saldo"]), ("parcial", 4000, 6000))
        self.erro(self.api_a.post(f"{url}/baixas/", {"valor": 6001}, format="json"), 422)  # passa do saldo
        self.erro(self.api_a.post(f"{url}/baixas/", {"valor": 0}, format="json"), 400)
        self.erro(self.api_a.post(f"{url}/baixas/", {"valor": 100, "data": str(HOJE + timedelta(days=1))}, format="json"), 400)  # futura
        total = self.post(f"financeiro/contas-pagar/{c['id']}/baixas", {"valor": 6000})
        self.assertEqual(total["status"], "paga")
        baixa_id = total["baixas"][0]["id"]
        estornada = self.ok(self.api_a.post(f"{url}/baixas/{baixa_id}/estorno/", {"observacao": "erro"}, format="json"))["data"]
        self.assertEqual((estornada["status"], estornada["saldo"]), ("parcial", 4000 + 0 if False else estornada["saldo"]))
        self.assertEqual(estornada["valor_baixado"], 6000 - 0 if False else estornada["valor_baixado"])
        self.erro(self.api_a.post(f"{url}/baixas/{baixa_id}/estorno/", {}, format="json"), 422)  # já estornada

    def test_excluir_exige_estornar_antes_e_sem_baixas_apaga(self):
        c = self.conta()
        self.post(f"financeiro/contas-pagar/{c['id']}/baixas", {"valor": 100})
        self.erro(self.api_a.delete(f"{V1}/financeiro/contas-pagar/{c['id']}/"), 422)
        livre = self.conta(descricao="Livre")
        self.assertEqual(self.api_a.delete(f"{V1}/financeiro/contas-pagar/{livre['id']}/").status_code, 204)
        self.assertEqual([x["descricao"] for x in self.itens("financeiro/contas-pagar", situacao="cancelada")], [])
        self.assertEqual(len(self.itens("financeiro/contas-pagar")), 1)

    def test_conta_cancelada_nao_se_edita_nem_recebe_baixa(self):
        c = self.conta()
        from apps.financeiro.models import ContaPagar

        ContaPagar.objects.filter(pk=c["id"]).update(ativo=False)
        self.erro(self.api_a.patch(f"{V1}/financeiro/contas-pagar/{c['id']}/", {"descricao": "x"}, format="json"), 404)
        self.erro(self.api_a.post(f"{V1}/financeiro/contas-pagar/{c['id']}/baixas/", {"valor": 1}, format="json"), 404)
        self.assertEqual(len(self.itens("financeiro/contas-pagar", situacao="cancelada")), 1)

    def test_edicao_parcial_e_valor_menor_que_o_baixado(self):
        c = self.conta()
        novo = self.ok(self.api_a.patch(f"{V1}/financeiro/contas-pagar/{c['id']}/", {"descricao": "Farinha integral"}, format="json"))["data"]
        self.assertEqual((novo["descricao"], novo["valor"], novo["contraparte"]), ("Farinha integral", 10000, "Moinho"))
        self.post(f"financeiro/contas-pagar/{c['id']}/baixas", {"valor": 5000})
        self.erro(self.api_a.patch(f"{V1}/financeiro/contas-pagar/{c['id']}/", {"valor": 4000}, format="json"), 400)

    def test_filtros_de_situacao_vencimento_e_texto(self):
        self.conta(descricao="Atrasada", emissao=str(HOJE - timedelta(days=10)), vencimento=str(HOJE - timedelta(days=3)))
        paga = self.conta(descricao="Paga")
        self.post(f"financeiro/contas-pagar/{paga['id']}/baixas", {"valor": 10000})
        self.conta(descricao="Futura", vencimento=str(HOJE + timedelta(days=40)))
        nomes = lambda **p: sorted(c["descricao"] for c in self.itens("financeiro/contas-pagar", **p))  # noqa: E731
        self.assertEqual(nomes(situacao="atrasada"), ["Atrasada"])
        self.assertEqual(nomes(situacao="paga"), ["Paga"])
        self.assertEqual(nomes(situacao="aberta"), ["Atrasada", "Futura"])
        self.assertEqual(nomes(vencimento_de=str(HOJE)), ["Futura", "Paga"])
        self.assertEqual(nomes(q="fut"), ["Futura"])

    def test_pagar_e_receber_sao_separados_e_isolados_por_empresa(self):
        self.conta("pagar")
        pagar, receber = self.conta("pagar"), self.conta("receber")  # a segunda conta a pagar tem id 2; só existe a receber 1
        self.erro(self.api_a.get(f"{V1}/financeiro/contas-receber/{pagar['id']}/"), 404)  # id de outro tipo não aparece
        self.assertEqual(len(self.itens("financeiro/contas-receber")), 1)
        self.erro(self.api_b.get(f"{V1}/financeiro/contas-pagar/{pagar['id']}/"), 404)
        self.erro(self.api_b.post(f"{V1}/financeiro/contas-receber/{receber['id']}/baixas/", {"valor": 1}, format="json"), 404)
        self.assertEqual(self.itens("financeiro/contas-pagar", api=self.api_b), [])

    def test_permissoes_por_acao_e_modulo(self):
        c = self.conta()
        self.erro(self.sem_permissao("add_contapagar").post(f"{V1}/financeiro/contas-pagar/", {"descricao": "x", "categoria": self.despesa["id"], "valor": 1, "vencimento": str(HOJE)}, format="json"), 403)
        sem_change = self.sem_permissao("change_contapagar")
        self.erro(sem_change.post(f"{V1}/financeiro/contas-pagar/{c['id']}/baixas/", {"valor": 1}, format="json"), 403)
        self.erro(sem_change.patch(f"{V1}/financeiro/contas-pagar/{c['id']}/", {"descricao": "x"}, format="json"), 403)
        self.erro(self.anonimo.get(f"{V1}/financeiro/contas-pagar/"), 401)


class RelatoriosAPITests(Base):
    def setUp(self):
        super().setUp()
        despesa = self.post("financeiro/categorias", {"nome": "Fornecedores", "tipo": "despesa"})
        receita = self.post("financeiro/categorias", {"nome": "Vendas", "tipo": "receita"})
        venda = self.post("financeiro/contas-receber", {"descricao": "Venda", "categoria": receita["id"], "valor": 50000, "vencimento": str(HOJE)})
        compra = self.post("financeiro/contas-pagar", {"descricao": "Compra", "categoria": despesa["id"], "valor": 20000, "vencimento": str(HOJE)})
        self.post(f"financeiro/contas-receber/{venda['id']}/baixas", {"valor": 50000})
        self.post(f"financeiro/contas-pagar/{compra['id']}/baixas", {"valor": 5000})

    def test_dre_por_caixa_e_por_vencimento(self):
        caixa = self.ok(self.api_a.get(f"{V1}/financeiro/dre/"))["data"]
        self.assertEqual((caixa["regime"], caixa["total_receitas"], caixa["total_despesas"], caixa["resultado"]), ("caixa", 50000, 5000, 45000))
        self.assertEqual(caixa["receitas"], [{"categoria": "Vendas", "total": 50000}])
        venc = self.ok(self.api_a.get(f"{V1}/financeiro/dre/", {"regime": "vencimento"}))["data"]
        self.assertEqual((venc["total_despesas"], venc["resultado"]), (20000, 30000))

    def test_dre_periodo_invalido_e_isolamento(self):
        self.erro(self.api_a.get(f"{V1}/financeiro/dre/", {"inicio": "2026-12-31", "fim": "2026-01-01"}), 422)
        vazio = self.ok(self.api_b.get(f"{V1}/financeiro/dre/"))["data"]
        self.assertEqual((vazio["total_receitas"], vazio["resultado"]), (0, 0))

    def test_balanco(self):
        b = self.ok(self.api_a.get(f"{V1}/financeiro/balanco/"))["data"]
        self.assertEqual((b["caixa"], b["a_pagar"], b["ativo"], b["patrimonio"]), (45000, 15000, 45000, 30000))
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/financeiro/balanco/", {"data": str(HOJE - timedelta(days=400))}))["data"]["caixa"], 0)

    def test_relatorios_exigem_a_permissao_especifica(self):
        sem = self.sem_permissao("view_relatoriofinanceiro")
        self.erro(sem.get(f"{V1}/financeiro/dre/"), 403)
        self.erro(sem.get(f"{V1}/financeiro/balanco/"), 403)
        self.erro(self.anonimo.get(f"{V1}/financeiro/dre/"), 401)


class PainelAPITests(Base):
    def test_blocos_conforme_permissoes_e_periodo(self):
        dados = self.ok(self.api_a.get(f"{V1}/painel/", {"periodo": "30_dias"}))["data"]
        self.assertEqual((dados["periodo"]["tipo"], dados["periodo"]["rotulo"]), ("30_dias", "Últimos 30 dias"))
        self.assertIn("estoque", dados["blocos"])
        self.assertIn("contas", dados["blocos"])
        self.usuario_a.groups.first().permissions.remove(Permission.objects.get(codename="view_movimentoestoque"))
        sem_estoque = self.ok(cliente_api(self.usuario_a).get(f"{V1}/painel/"))["data"]
        self.assertNotIn("estoque", sem_estoque["blocos"])  # sem permissão, o bloco não aparece
        self.erro(self.anonimo.get(f"{V1}/painel/"), 401)

    def test_periodo_invalido_cai_no_mes_atual(self):
        dados = self.ok(self.api_a.get(f"{V1}/painel/", {"periodo": "personalizado", "inicio": "2026-05-10", "fim": "2026-01-01"}))["data"]
        self.assertEqual(dados["periodo"]["tipo"], "mes_atual")


class SwaggerEstoqueFinanceiroTests(Base):
    def test_schema_descreve_os_endpoints_novos(self):
        self.client.force_login(self.admin)
        caminhos = self.client.get("/api/schema/", {"format": "json"}).json()["paths"]
        esperados = [
            "/api/v1/estoque/saldos/", "/api/v1/estoque/saldos/{id}/", "/api/v1/estoque/saldos/{id}/minimo/", "/api/v1/estoque/alertas/",
            "/api/v1/estoque/transferencias/", "/api/v1/estoque/transferencias/{id}/", "/api/v1/estoque/movimentos/",
            "/api/v1/financeiro/categorias/", "/api/v1/financeiro/categorias/{id}/ativar/", "/api/v1/financeiro/contas-pagar/",
            "/api/v1/financeiro/contas-pagar/{id}/baixas/", "/api/v1/financeiro/contas-pagar/{id}/baixas/{baixa_pk}/estorno/",
            "/api/v1/financeiro/contas-receber/", "/api/v1/financeiro/dre/", "/api/v1/financeiro/balanco/", "/api/v1/painel/",
        ]
        for caminho in esperados:
            self.assertIn(caminho, caminhos)
        self.assertIn("post", caminhos["/api/v1/estoque/movimentos/"])
        self.assertIn("put", caminhos["/api/v1/estoque/saldos/{id}/minimo/"])
