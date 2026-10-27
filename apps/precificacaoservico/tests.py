"""Precificação de serviços: fórmulas (custo-hora, preço por margem e markup, repasses, alertas), snapshot das premissas (propostas antigas não
mudam), ciclo de vida, execução e previsto x realizado, segmento, permissões e isolamento entre unidades (telas e API)."""
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.modulo.models import Segmento
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import calculos as c
from .models import ConfiguracaoServico, DespesaFixa, Proposta

D = Decimal
V1 = "/api/v1/precificacao-servico"
PREMISSAS = {"horas_dia": "8", "horas_faturaveis": "100", "custo_hora_total": "6000", "impostos_pct": "6", "taxas_pct": "4", "margem_pct": "20"}


class CalculosTests(SimpleTestCase):
    def calcular(self, itens=None, custos=None, premissas=None, **extra):
        return c.calcular_proposta({"itens": itens if itens is not None else [{"modo": "hora", "quantidade": "5"}], "custos": custos or [], **extra}, premissas or PREMISSAS)

    def test_custo_hora_separa_remuneracao_e_divide_pelas_horas_faturaveis(self):
        cap = c.capacidade(20, 8, 60)  # 160 disponíveis, 100 faturáveis
        self.assertEqual((cap["horas_disponiveis"], cap["horas_faturaveis"]), (160, 100))
        ch = c.custo_hora(400000, D(100000), D(0), cap["horas_faturaveis"], 500)
        self.assertEqual(ch["custo_fixo_mensal"], 500000)
        self.assertEqual((ch["custo_fixo_hora"], ch["custo_hora_total"], ch["remuneracao_hora"]), (5000, 5500, 4000))

    def test_horas_faturaveis_invalidas_bloqueiam(self):
        for args in [(20, 8, 160), (20, 8, 200), (0, 8, 0), (20, 25, 0), (20, 8, -1)]:
            with self.assertRaises(RegraNegocioError):
                c.capacidade(*args)
        with self.assertRaises(RegraNegocioError):
            c.capacidade(20, 8, 0, 170)  # faturáveis informadas acima das disponíveis
        with self.assertRaises(RegraNegocioError):
            c.custo_hora(1000, 0, 0, 0)

    def test_periodicidade_para_base_mensal(self):
        self.assertEqual(c.valor_mensal(120000, "anual"), 10000)
        self.assertEqual(c.valor_mensal(30000, "trimestral"), 10000)
        self.assertEqual(c.centavos(c.valor_mensal(1000, "semanal")), 4333)
        with self.assertRaises(RegraNegocioError):
            c.valor_mensal(100, "diaria")

    def test_exemplo_da_especificacao_margem_sobre_o_preco(self):
        self.assertEqual(c.preco_por_margem(50000, 6, 4, 20), 71429)  # R$ 714,29
        r = c.calcular_proposta({"itens": [], "custos": [{"repasse": "incluido", "quantidade": 1, "custo_unitario": 50000}]}, PREMISSAS)
        self.assertEqual((r["custo_total"], r["total"], r["impostos_taxas"], r["lucro"], r["margem"]), (50000, 71429, 7143, 14286, "20.00"))

    def test_exemplo_do_pacote_do_tecnico(self):
        premissas = {**PREMISSAS, "custo_hora_total": "6000"}
        r = self.calcular(custos=[{"repasse": "incluido", "quantidade": 1, "custo_unitario": 18000}, {"repasse": "incluido", "quantidade": 1, "custo_unitario": 4000}], premissas=premissas)
        self.assertEqual((r["custo_trabalho"], r["custo_total"]), (30000, 52000))
        self.assertEqual(r["preco_alvo"], 74286)  # R$ 742,86

    def test_preco_fechado_recalcula_margem_e_alerta(self):
        r = self.calcular(custos=[{"repasse": "incluido", "quantidade": 1, "custo_unitario": 22000}], preco_fechado=60000)
        self.assertEqual(r["origem_preco"], "preco_fechado")
        self.assertEqual(r["total"], 60000)
        self.assertEqual(r["lucro"], 60000 - 52000 - 6000)
        self.assertIn("abaixo_da_margem", [a["codigo"] for a in r["alertas"]])
        abaixo = self.calcular(preco_fechado=20000)
        self.assertIn("abaixo_do_custo", [a["codigo"] for a in abaixo["alertas"]])
        self.assertLess(abaixo["lucro"], 0)

    def test_margem_e_markup_nao_se_misturam(self):
        custo = [{"repasse": "incluido", "quantidade": 1, "custo_unitario": 1000}]
        margem = c.calcular_proposta({"itens": [], "custos": custo, "margem_pct": 50}, {**PREMISSAS, "impostos_pct": "0", "taxas_pct": "0"})
        markup = c.calcular_proposta({"itens": [], "custos": custo, "metodo_preco": "markup", "markup_pct": 50}, {**PREMISSAS, "impostos_pct": "0", "taxas_pct": "0"})
        self.assertEqual((margem["total"], markup["total"]), (2000, 1500))
        self.assertEqual(markup["margem"], "33.33")  # a margem resultante do markup de 50% é 33,33%
        self.assertEqual(markup["markup_resultante"], "50.00")

    def test_percentuais_impossiveis_sao_bloqueados(self):
        for kwargs in [{"margem_pct": 90}, {"custo_financeiro_pct": 95}]:
            with self.assertRaises(RegraNegocioError):
                self.calcular(**kwargs)
        with self.assertRaises(RegraNegocioError):
            c.validar_percentuais(50, 30, 20)  # soma exatamente 100%
        with self.assertRaises(RegraNegocioError):
            c.validar_percentuais(-1, 0, 10)

    def test_repasse_discriminado_incluido_e_absorvido(self):
        base = self.calcular(itens=[{"modo": "hora", "quantidade": "1"}])
        incluido = self.calcular(itens=[{"modo": "hora", "quantidade": "1"}], custos=[{"repasse": "incluido", "quantidade": 1, "custo_unitario": 7000}])
        absorvido = self.calcular(itens=[{"modo": "hora", "quantidade": "1"}], custos=[{"repasse": "absorvido", "quantidade": 1, "custo_unitario": 7000}])
        discriminado = self.calcular(itens=[{"modo": "hora", "quantidade": "1"}], custos=[{"repasse": "discriminado", "quantidade": 1, "custo_unitario": 7000}])
        self.assertGreater(incluido["total"], base["total"])
        self.assertEqual(absorvido["total"], base["total"])  # absorvido pesa no custo, não no preço
        self.assertEqual(absorvido["custo_total"], base["custo_total"] + 7000)
        self.assertEqual(discriminado["total"], base["total"] + c.centavos(D(7000) / D("0.9"), c.ROUND_CEILING))  # repasse cobre impostos/taxas, sem margem
        self.assertLess(absorvido["lucro"], base["lucro"])

    def test_formas_de_cobranca_e_horas_padrao(self):
        dia = self.calcular(itens=[{"modo": "dia", "quantidade": "2"}])
        self.assertEqual(dia["horas_trabalho"], "16.00")  # 2 dias x jornada de 8 h
        semana = self.calcular(itens=[{"modo": "semana", "quantidade": "1"}])
        self.assertEqual(semana["horas_trabalho"], "40.00")
        mes = self.calcular(itens=[{"modo": "mes", "quantidade": "1"}])
        self.assertEqual(mes["horas_trabalho"], "100.00")
        pacote = self.calcular(itens=[{"modo": "pacote", "quantidade": "1", "horas_por_unidade": "12.5"}])
        self.assertEqual(pacote["horas_trabalho"], "12.50")

    def test_precos_unitarios_dos_itens_horas_minimas_e_arredondamento(self):
        r = self.calcular(itens=[{"modo": "hora", "quantidade": "1.1", "preco_unitario": 10000}], arredondamento_minutos=30, horas_minimas=2)
        self.assertEqual(r["origem_preco"], "precos_dos_itens")
        self.assertEqual(r["horas_trabalho"], "2.00")  # 1,1 h -> 1,5 h (blocos de 30 min) -> mínimo de 2 h
        self.assertEqual(r["total"], 20000)
        minimo = self.calcular(itens=[{"modo": "hora", "quantidade": "1", "preco_unitario": 1000}], minimo_cobranca=15000)
        self.assertEqual((minimo["origem_preco"], minimo["total"]), ("cobranca_minima", 15000))

    def test_desconto_acrescimo_e_margem_negativa(self):
        r = self.calcular(desconto=10, desconto_tipo="percentual", acrescimo_pct=5, acrescimo_valor=1000)
        bruto = r["preco_base"] + c.centavos(D(r["preco_base"]) * D("0.05")) + 1000
        self.assertEqual(r["total"], bruto - r["desconto"])
        self.assertEqual(r["desconto"], c.centavos(D(bruto) * D("0.10")))
        prejuizo = self.calcular(desconto=r["preco_alvo"] - 1000)
        self.assertIn("desconto_margem_negativa", [a["codigo"] for a in prejuizo["alertas"]])
        with self.assertRaises(RegraNegocioError):
            self.calcular(desconto=99999999)  # desconto maior que a proposta
        with self.assertRaises(RegraNegocioError):
            self.calcular(desconto=-1)

    def test_entradas_invalidas(self):
        for itens in [[{"modo": "xx", "quantidade": "1"}], [{"modo": "hora", "quantidade": "0"}], [{"modo": "hora", "quantidade": "abc"}], [{"modo": "hora", "quantidade": 1.5}], [{"modo": "hora", "quantidade": "1", "preco_unitario": -5}]]:
            with self.assertRaises(RegraNegocioError):
                self.calcular(itens=itens)
        with self.assertRaises(RegraNegocioError):
            self.calcular(itens=[], custos=[])
        with self.assertRaises(RegraNegocioError):
            self.calcular(custos=[{"repasse": "nenhum", "quantidade": 1, "custo_unitario": 1}])


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = cliente
    u.profile.save()
    u.groups.add(Group.objects.get(name=grupo))
    return u


class Base(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        servico = Segmento.objects.get(slug="servico")
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Servicos A", segmento=servico)
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Servicos B", segmento=servico)
        self.padaria = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11555666000100", nome_razao_social="Padaria", segmento=Segmento.objects.get(slug="produto"))
        for cli in (self.a, self.b):
            habilitar_modulo(cli, "precificacao-servico")
        self.gestor = usuario("gestor_a", self.a, "Gestor")
        self.operador = usuario("operador_a", self.a, "Operador")
        self.admin = usuario("admin_a", self.a, "Administrador")
        self.gestor_b = usuario("gestor_b", self.b, "Gestor")
        self.gestor_padaria = usuario("gestor_p", self.padaria, "Gestor")
        cfg = ConfiguracaoServico.da_unidade(self.a)
        cfg.remuneracao_mensal, cfg.dias_mes, cfg.horas_dia, cfg.horas_nao_faturaveis = 400000, D("20"), D("8"), D("60")
        cfg.impostos_pct, cfg.taxas_pct, cfg.margem_pct = D("6"), D("4"), D("20")
        cfg.save()
        DespesaFixa.objects.create(cliente=self.a, descricao="Aluguel", valor=100000, periodicidade="mensal")  # custo-hora = 5000/h
        self.client.force_login(self.gestor)

    def api(self, user):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=user)[0].key}")
        return api

    def corpo(self, **extra):
        return {"destinatario_nome": "Maria", "descricao": "Instalação elétrica", "modo": "hora", "itens": [{"descricao": "Mão de obra", "modo": "hora", "quantidade": "5"}],
                "custos": [{"descricao": "Peças", "categoria": "material", "quantidade": "1", "custo_unitario": 18000, "repasse": "incluido"}], **extra}

    def criar(self, user=None, **extra):
        resposta = self.api(user or self.gestor).post(f"{V1}/propostas/", self.corpo(**extra), format="json")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        return Proposta.objects.get(pk=resposta.json()["data"]["id"])


class CustoHoraEConfigTests(Base):
    def test_custo_hora_pela_api(self):
        dados = self.api(self.gestor).get(f"{V1}/custo-hora/").json()["data"]
        self.assertEqual((dados["custo_hora_total"], dados["remuneracao_hora"]), (5000, 4000))
        self.assertEqual(D(dados["horas"]["horas_faturaveis"]), 100)

    def test_configuracao_invalida_nao_grava(self):
        api = self.api(self.gestor)
        for corpo in [{"horas_nao_faturaveis": "160"}, {"margem_pct": "95"}, {"moeda": "USD"}, {"impostos_pct": "-1"}]:
            self.assertIn(api.patch(f"{V1}/configuracao/", corpo, format="json").status_code, (400, 422), corpo)
        ok = api.patch(f"{V1}/configuracao/", {"margem_pct": "25", "remuneracao_mensal": 500000}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        cfg = ConfiguracaoServico.objects.get(cliente=self.a)
        self.assertEqual((cfg.margem_pct, cfg.remuneracao_mensal, cfg.versao), (D("25"), 500000, 2))
        self.assertTrue(RegistroAuditoria.objects.filter(tabela="precificacaoservico_configuracaoservico").exists())

    def test_tela_de_configuracao_mostra_a_composicao(self):
        pagina = self.client.get(reverse("ps-config"))
        self.assertContains(pagina, "Custo-hora total")
        self.assertContains(pagina, "R$ 50,00")


class PropostaTests(Base):
    def test_cria_calcula_no_servidor_e_ignora_campos_internos(self):
        p = self.criar(total=1, custo_total=1, status="aprovada", cliente=self.b.pk, numero=99)
        self.assertEqual((p.cliente_id, p.status, p.numero), (self.a.pk, "rascunho", 1))
        # trabalho 5 h x R$ 50 = 25000 + peças 18000 = 43000; preço-alvo = 43000 / 0,7
        self.assertEqual((p.custo_total, p.total), (43000, 61429))
        self.assertEqual(p.premissas["custo_hora_total"], "5000")
        self.assertEqual(p.premissas["versao_configuracao"], 1)

    def test_mudar_configuracao_nao_altera_proposta_antiga_ate_atualizar_premissas(self):
        p = self.criar()
        api = self.api(self.gestor)
        api.patch(f"{V1}/configuracao/", {"remuneracao_mensal": 800000, "margem_pct": "30"}, format="json")
        despesa = DespesaFixa.objects.get(cliente=self.a)
        api.patch(f"{V1}/despesas/{despesa.pk}/", {"valor": 300000}, format="json")
        editada = api.patch(f"{V1}/propostas/{p.pk}/", {"observacoes": "Cliente pediu urgência"}, format="json")
        self.assertEqual(editada.status_code, 200, editada.content)
        p.refresh_from_db()
        self.assertEqual((p.total, p.premissas["custo_hora_total"], p.premissas["margem_pct"]), (61429, "5000", "20.00"))
        novo = self.api(self.gestor).post(f"{V1}/simular/", self.corpo(), format="json").json()["data"]
        self.assertGreater(novo["resultado"]["total"], 61429)  # o simulador usa as premissas de hoje
        api.patch(f"{V1}/propostas/{p.pk}/", {"atualizar_premissas": True}, format="json")
        p.refresh_from_db()
        self.assertGreater(p.total, 61429)
        self.assertEqual(p.premissas["versao_configuracao"], 2)

    def test_edicao_sem_linhas_mantem_itens_e_custos(self):
        p = self.criar()
        self.api(self.gestor).patch(f"{V1}/propostas/{p.pk}/", {"desconto": "1000", "desconto_tipo": "valor"}, format="json")
        p.refresh_from_db()
        self.assertEqual((p.itens.count(), p.custos.count(), p.resultado["desconto"], p.total), (1, 1, 1000, 60429))

    def test_invalidos_sao_recusados_no_servidor(self):
        api = self.api(self.gestor)
        for extra in [{"margem_pct": "95"}, {"desconto": "-5"}, {"itens": [], "custos": []}, {"destinatario_nome": ""}, {"modo": "x"}, {"preco_fechado": -1}]:
            self.assertGreaterEqual(api.post(f"{V1}/propostas/", self.corpo(**extra), format="json").status_code, 400, extra)
        self.assertEqual(Proposta.objects.count(), 0)

    def test_ciclo_de_vida_e_execucao_previsto_x_realizado(self):
        p = self.criar()
        api = self.api(self.gestor)
        self.assertEqual(api.post(f"{V1}/propostas/{p.pk}/lancamentos/", {"tipo": "horas", "data": "2026-10-20", "horas": "4"}, format="json").status_code, 422)  # só após aprovar
        self.assertEqual(api.post(f"{V1}/propostas/{p.pk}/status/", {"status": "concluida"}, format="json").status_code, 422)
        aprovada = api.post(f"{V1}/propostas/{p.pk}/status/", {"status": "aprovada", "aceite_nome": "Maria da Silva"}, format="json")
        self.assertEqual(aprovada.status_code, 200, aprovada.content)
        p.refresh_from_db()
        self.assertEqual((p.status, p.aceite_nome), ("aprovada", "Maria da Silva"))
        self.assertEqual(api.patch(f"{V1}/propostas/{p.pk}/", {"observacoes": "x"}, format="json").status_code, 422)  # aprovada não se edita
        # horas sem custo informado valem horas x custo-hora do RETRATO; peças reais mais caras; extra aprovado; pagamento com taxa
        l1 = api.post(f"{V1}/propostas/{p.pk}/lancamentos/", {"tipo": "horas", "data": "2026-10-20", "horas": "6"}, format="json")
        self.assertEqual(l1.json()["data"]["custo"], 30000)
        api.post(f"{V1}/propostas/{p.pk}/lancamentos/", {"tipo": "custo", "data": "2026-10-21", "custo": 20000, "descricao": "Peças"}, format="json")
        api.post(f"{V1}/propostas/{p.pk}/lancamentos/", {"tipo": "extra", "data": "2026-10-22", "horas": "1", "receita": 8000}, format="json")
        self.assertEqual(api.post(f"{V1}/propostas/{p.pk}/lancamentos/", {"tipo": "custo", "data": "2026-10-21", "custo": 100, "receita": 5}, format="json").status_code, 422)
        api.post(f"{V1}/propostas/{p.pk}/pagamentos/", {"valor": 30000, "data": "2026-10-21", "meio": "cartao", "taxa": 1200}, format="json")
        api.post(f"{V1}/propostas/{p.pk}/pagamentos/", {"valor": 39429, "data": "2026-11-21", "meio": "pix", "status": "pendente"}, format="json")
        self.assertEqual(api.post(f"{V1}/propostas/{p.pk}/pagamentos/", {"valor": 100, "data": "2026-10-21", "meio": "pix", "taxa": 200}, format="json").status_code, 422)
        rel = api.get(f"{V1}/propostas/{p.pk}/relatorio/").json()["data"]
        self.assertEqual(rel["realizado"]["custo"], 30000 + 20000 + 5000)
        self.assertEqual(rel["variacao_custo"], 55000 - 43000)
        self.assertEqual((rel["recebido"], rel["pendente"], rel["impostos_taxas_registrados"]), (30000, 39429, 1200))
        self.assertEqual(rel["resultado"], 30000 - 55000 - 1200)  # recebido - custos reais - taxas registradas
        self.assertEqual(rel["realizado"]["valor_total"], 61429 + 8000)
        concluida = api.post(f"{V1}/propostas/{p.pk}/status/", {"status": "concluida"}, format="json")
        self.assertEqual(concluida.status_code, 200)
        p.refresh_from_db()
        self.assertEqual((p.data_inicio.isoformat(), p.data_conclusao is not None), ("2026-10-20", True))
        # o realizado não mexe em preços futuros: a configuração segue igual
        self.assertEqual(ConfiguracaoServico.objects.get(cliente=self.a).versao, 1)

    def test_excluir_so_rascunho_e_cancelar_preserva_historico(self):
        p, q = self.criar(), self.criar()
        api = self.api(self.admin)
        self.assertEqual(api.delete(f"{V1}/propostas/{p.pk}/").status_code, 204)
        self.api(self.gestor).post(f"{V1}/propostas/{q.pk}/status/", {"status": "enviada"}, format="json")
        self.assertEqual(api.delete(f"{V1}/propostas/{q.pk}/").status_code, 422)
        self.assertEqual(self.api(self.gestor).post(f"{V1}/propostas/{q.pk}/status/", {"status": "cancelada"}, format="json").status_code, 200)
        self.assertTrue(Proposta.objects.filter(pk=q.pk).exists())
        self.assertEqual(self.api(self.gestor).delete(f"{V1}/propostas/{q.pk}/").status_code, 403)  # excluir é do Administrador

    def test_numeracao_por_unidade_e_auditoria(self):
        self.criar()
        self.assertEqual(self.criar().numero, 2)
        self.assertTrue(RegistroAuditoria.objects.filter(tabela="precificacaoservico_proposta", acao="criar").exists())


class IsolamentoEPermissoesTests(Base):
    def test_outra_unidade_recebe_404_em_tudo(self):
        p = self.criar()
        outro = self.api(self.gestor_b)
        habilitar_modulo(self.b, "precificacao-servico")
        for metodo, url, corpo in [("get", f"propostas/{p.pk}/", None), ("patch", f"propostas/{p.pk}/", {"observacoes": "x"}), ("post", f"propostas/{p.pk}/status/", {"status": "enviada"}),
                                   ("get", f"propostas/{p.pk}/relatorio/", None), ("get", f"propostas/{p.pk}/lancamentos/", None), ("post", f"propostas/{p.pk}/pagamentos/", {"valor": 1, "data": "2026-10-10", "meio": "pix"})]:
            resposta = getattr(outro, metodo)(f"{V1}/{url}", corpo, format="json") if corpo else getattr(outro, metodo)(f"{V1}/{url}")
            self.assertEqual(resposta.status_code, 404, (metodo, url))
        self.assertEqual(outro.get(f"{V1}/propostas/").json()["data"]["paginacao"]["total"], 0)
        self.assertEqual(self.client.get(reverse("ps-proposta-detalhe", args=[p.pk])).status_code, 200)
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("ps-proposta-detalhe", args=[p.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("ps-proposta-imprimir", args=[p.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("ps-proposta-acao", args=[p.pk, "status"]), {"novo": "enviada"}).status_code, 404)

    def test_consumidor_de_outra_unidade_e_recusado(self):
        from apps.consumidor.models import Consumidor

        alheio = Consumidor.objects.create(cliente=self.b, nome="Cliente da B")
        resposta = self.api(self.gestor).post(f"{V1}/propostas/", self.corpo(consumidor=alheio.pk), format="json")
        self.assertEqual(resposta.status_code, 400)

    def test_despesa_e_modelo_isolados_por_unidade(self):
        despesa = DespesaFixa.objects.get(cliente=self.a)
        outro = self.api(self.gestor_b)
        self.assertEqual(outro.get(f"{V1}/despesas/{despesa.pk}/").status_code, 404)
        self.assertEqual(outro.patch(f"{V1}/despesas/{despesa.pk}/", {"valor": 1}, format="json").status_code, 404)
        criado = outro.post(f"{V1}/despesas/", {"descricao": "Internet", "valor": 12000}, format="json")
        self.assertEqual(criado.status_code, 201, criado.content)
        self.assertEqual(DespesaFixa.objects.get(pk=criado.json()["data"]["id"]).cliente_id, self.b.pk)

    def test_perfis(self):
        p = self.criar()
        operador = self.api(self.operador)
        self.assertEqual(operador.get(f"{V1}/propostas/").status_code, 403)  # remuneração e margem são sensíveis: sem permissão
        self.assertEqual(operador.get(f"{V1}/configuracao/").status_code, 403)
        self.assertEqual(operador.post(f"{V1}/simular/", self.corpo(), format="json").status_code, 403)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("ps-proposta-list")).status_code, 403)
        self.assertEqual(self.api(self.gestor).get(f"{V1}/propostas/{p.pk}/").status_code, 200)

    def test_segmento_de_produto_nao_usa_o_modulo(self):
        padaria = self.api(self.gestor_padaria)
        for url in ("propostas/", "configuracao/", "custo-hora/", "despesas/", "modelos/"):
            self.assertEqual(padaria.get(f"{V1}/{url}").status_code, 403, url)
        self.assertEqual(padaria.post(f"{V1}/simular/", self.corpo(), format="json").status_code, 403)
        self.client.force_login(self.gestor_padaria)
        for rota in ("ps-proposta-list", "ps-config", "ps-despesa-list", "ps-modelo-list", "ps-proposta-create"):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 403, rota)
        # Mesmo com o módulo habilitado por engano, o segmento barra (servidor, não só menu).
        habilitar_modulo(self.padaria, "precificacao-servico")
        self.assertEqual(self.client.get(reverse("ps-proposta-list")).status_code, 403)
        self.assertEqual(padaria.get(f"{V1}/propostas/").status_code, 403)

    def test_modulo_desabilitado_para_o_cliente(self):
        habilitar_modulo(self.a, "precificacao-servico", ativo=False)
        self.assertEqual(self.api(self.gestor).get(f"{V1}/propostas/").status_code, 403)
        self.assertEqual(self.client.get(reverse("ps-proposta-list")).status_code, 403)

    def test_modulo_so_nos_segmentos_de_servico(self):
        from apps.modulo.models import Modulo

        modulo = Modulo.objects.get(slug="precificacao-servico")
        self.assertEqual(set(modulo.segmentos.values_list("slug", flat=True)) & {"servico", "ambos", "produto"}, {"servico", "ambos"})


class TelasTests(Base):
    def post_proposta(self, **extra):
        itens = [{"descricao": "Mão de obra", "modo": "hora", "quantidade": "5", "unidade": "", "horas_por_unidade": "", "preco_unitario": ""}]
        custos = [{"descricao": "Peças", "categoria": "material", "quantidade": "1", "unidade": "", "custo_unitario": "180,00", "repasse": "incluido"}]
        dados = {"destinatario_nome": "Maria", "descricao": "Instalação", "modo": "hora", "metodo_preco": "margem", "arredondamento_minutos": "0", "desconto_tipo": "valor",
                 "itens_json": json.dumps(itens), "custos_json": json.dumps(custos), **extra}
        return self.client.post(reverse("ps-proposta-create"), dados)

    def test_lista_formulario_detalhe_e_impressao(self):
        for rota in ("ps-proposta-list", "ps-proposta-create", "ps-config", "ps-despesa-list", "ps-despesa-create", "ps-modelo-list", "ps-modelo-create"):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 200, rota)
        resposta = self.post_proposta()
        self.assertEqual(resposta.status_code, 302, getattr(resposta, "context", None) and resposta.context["form"].errors)
        p = Proposta.objects.get()
        self.assertEqual((p.cliente_id, p.total, p.criado_por_id), (self.a.pk, 61429, self.gestor.pk))
        detalhe = self.client.get(reverse("ps-proposta-detalhe", args=[p.pk]))
        self.assertContains(detalhe, "R$ 614,29")
        self.assertContains(self.client.get(reverse("ps-proposta-edit", args=[p.pk])), "formProposta")
        imprimir = self.client.get(reverse("ps-proposta-imprimir", args=[p.pk]))
        self.assertContains(imprimir, "PROPOSTA Nº 1")
        self.assertNotContains(imprimir, "Lucro")  # custos internos, lucro e margem não vão ao cliente
        self.assertNotContains(imprimir, "Margem")

    def test_previa_de_calculo_nao_grava(self):
        itens = [{"descricao": "x", "modo": "hora", "quantidade": "5"}]
        resposta = self.client.post(reverse("ps-proposta-calcular"), {"destinatario_nome": "M", "descricao": "d", "modo": "hora", "metodo_preco": "margem", "arredondamento_minutos": "0",
                                                                       "desconto_tipo": "valor", "itens_json": json.dumps(itens), "custos_json": "[]"})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json()["resultado"]["custo_total"], 25000)
        self.assertEqual(Proposta.objects.count(), 0)

    def test_previa_incompleta_devolve_aviso_sem_status_de_erro(self):
        # custo sem valor durante a digitação: o painel mostra o aviso, mas a requisição não falha (nada de 4xx no console)
        custos = [{"descricao": "x", "categoria": "material", "repasse": "incluido", "quantidade": "1", "custo_unitario": ""}]
        resposta = self.client.post(reverse("ps-proposta-calcular"), {"destinatario_nome": "M", "descricao": "d", "modo": "hora", "metodo_preco": "margem", "arredondamento_minutos": "0",
                                                                       "desconto_tipo": "valor", "itens_json": "[]", "custos_json": json.dumps(custos)})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.json()["ok"])

    def test_erro_de_regra_volta_ao_formulario_sem_gravar(self):
        resposta = self.post_proposta(margem_pct="95")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "menor que 100%")
        self.assertEqual(Proposta.objects.count(), 0)
        self.assertEqual(self.post_proposta(itens_json="não é json").status_code, 200)
        self.assertEqual(Proposta.objects.count(), 0)

    def test_despesa_cria_e_entra_no_custo_hora(self):
        self.client.post(reverse("ps-despesa-create"), {"natureza": "provisao", "descricao": "Reserva de ferramentas", "categoria": "ferramentas", "valor": "1.000,00", "periodicidade": "mensal", "ativo": "on"})
        despesa = DespesaFixa.objects.get(descricao="Reserva de ferramentas")
        self.assertEqual((despesa.cliente_id, despesa.valor), (self.a.pk, 100000))
        self.assertContains(self.client.get(reverse("ps-config")), "R$ 60,00")  # (4.000 + 1.000 + 1.000) / 100 h

    def test_modelo_inicia_proposta_com_valores_copiados(self):
        self.client.post(reverse("ps-modelo-create"), {"nome": "Pintura", "modo": "unidade", "unidade": "m²", "preco_base": "35,00", "horas_por_unidade": "0,5", "margem_pct": "25", "ativo": "on"})
        modelo = self.a.modelos_servico.get()
        pagina = self.client.get(reverse("ps-proposta-create") + f"?modelo={modelo.pk}")
        self.assertContains(pagina, "Pintura")
        modelo_b = self.b.modelos_servico.create(nome="Da B")
        self.assertNotContains(self.client.get(reverse("ps-proposta-create") + f"?modelo={modelo_b.pk}"), "Da B")
