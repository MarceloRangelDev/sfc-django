"""Funções de mão de obra, itens de custo, vínculo com o colaborador (histórico e personalização), custo por período, simulações,
dashboard, tenant/permissões e API. As fórmulas estão documentadas em custos.py."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from web_project.regras import RegraNegocioError

from . import composicao, custos
from .models import Colaborador, ComponenteFuncao, FuncaoMaoDeObra, PersonalizacaoColaborador, SimulacaoPromocao, VinculoFuncao
from .tests import Base, V1, usuario

D = Decimal
HOJE = timezone.localdate()
ONTEM = HOJE - timedelta(days=1)
INICIO_ANTIGO = date(2026, 1, 1)


def item(categoria, nome="x", tipo=custos.FIXO, valor=None, percentual=None, base=custos.B_SALARIO, periodicidade=custos.MENSAL):
    return custos.Item(categoria=categoria, nome=nome, tipo=tipo, valor=valor, percentual=percentual, base=base, periodicidade=periodicidade)


class CustosPuroTests(SimpleTestCase):
    """Fórmulas: ordem das categorias, bases, anual ÷ 12, arredondamento por item e períodos."""

    def itens(self):
        return [
            item(custos.SALARIO, valor=300000),
            item(custos.ADICIONAL, tipo=custos.PERCENTUAL, percentual=D("30"), base=custos.B_SALARIO),  # 90.000
            item(custos.DSR, tipo=custos.PERCENTUAL, percentual=D("10"), base=custos.B_SAL_ADIC),  # 10% de 390.000 = 39.000
            item(custos.PROVISAO, tipo=custos.PERCENTUAL, percentual=D("8.3333"), base=custos.B_REMUNERACAO),  # 429.000 × 8,3333% = 35.749,86 -> 35.750
            item(custos.ENCARGO, tipo=custos.PERCENTUAL, percentual=D("8"), base=custos.B_REM_PROV),  # (429.000 + 35.750) × 8% = 37.180
            item(custos.BENEFICIO, valor=50000),
            item(custos.INDIRETO, valor=120000, periodicidade=custos.ANUAL),  # 120.000 ÷ 12 = 10.000
        ]

    def test_composicao_completa_sem_dupla_contagem(self):
        r = custos.calcular(self.itens(), D("220"), D("30"))
        self.assertEqual([i.mensal for i in r.itens], [300000, 90000, 39000, 35750, 37180, 50000, 10000])
        self.assertEqual(r.total_mensal, 561930)
        self.assertEqual(r.por_categoria[custos.ENCARGO], 37180)
        self.assertEqual(r.avisos, [])

    def test_periodos_a_partir_do_mensal(self):
        r = custos.calcular(self.itens(), D("220"), D("30"))
        self.assertEqual(r.periodos, {"dia": 18731, "semana": 131117, "quinzena": 280965, "mes": 561930, "bimestre": 1123860, "trimestre": 1685790, "semestre": 3371580, "ano": 6743160})
        self.assertEqual(r.custo_hora, 2554)  # 561.930 ÷ 220 = 2.554,22

    def test_dias_do_mes_configuraveis_e_arredondamento_meio_para_cima(self):
        self.assertEqual(custos.periodos_do_mes(1000, D("22"))["dia"], 45)  # 45,45 -> 45
        self.assertEqual(custos.periodos_do_mes(1005, D("30"))["dia"], 34)  # 33,5 -> 34 (meio para cima)
        with self.assertRaises(RegraNegocioError):
            custos.periodos_do_mes(1000, 0)

    def test_independe_da_ordem_em_que_os_itens_chegam(self):
        itens = self.itens()
        a = custos.calcular(itens, D("220"), D("30")).total_mensal
        b = custos.calcular(list(reversed(self.itens())), D("220"), D("30")).total_mensal
        self.assertEqual(a, b)

    def test_validacoes_de_item(self):
        invalidos = [
            (custos.ADICIONAL, custos.PERCENTUAL, None, D("10"), custos.B_REMUNERACAO, custos.MENSAL),  # base posterior ao adicional
            (custos.PROVISAO, custos.PERCENTUAL, None, D("10"), custos.B_REM_PROV, custos.MENSAL),  # provisão não usa provisões como base
            (custos.SALARIO, custos.PERCENTUAL, None, D("10"), custos.B_SALARIO, custos.MENSAL),  # salário é valor fixo
            (custos.SALARIO, custos.FIXO, 1000, None, custos.B_SALARIO, custos.ANUAL),  # salário é mensal
            (custos.ENCARGO, custos.FIXO, 1000, D("1"), custos.B_SALARIO, custos.MENSAL),  # valor E percentual
            (custos.ENCARGO, custos.PERCENTUAL, None, None, custos.B_SALARIO, custos.MENSAL),  # nenhum dos dois
            (custos.ENCARGO, custos.PERCENTUAL, None, D("-1"), custos.B_SALARIO, custos.MENSAL),
            ("inexistente", custos.FIXO, 1, None, custos.B_SALARIO, custos.MENSAL),
        ]
        for argumentos in invalidos:
            with self.assertRaises(RegraNegocioError, msg=argumentos):
                custos.validar_item(*argumentos)

    def test_sem_salario_avisa_dados_incompletos(self):
        r = custos.calcular([item(custos.ENCARGO, tipo=custos.PERCENTUAL, percentual=D("8"))], D("220"), D("30"))
        self.assertEqual(r.total_mensal, 0)
        self.assertTrue(r.avisos)

    def test_variacao(self):
        self.assertEqual(custos.variacao(1000, 1100), (100, D("10.00")))
        self.assertEqual(custos.variacao(0, 500), (500, None))
        self.assertEqual(custos.variacao(1000, 900), (-100, D("-10.00")))


class FuncoesBase(Base):
    """Cria uma função da unidade A com salário R$ 2.200,00 (hora R$ 10,00 com 220 h/mês) e alguns itens."""

    def setUp(self):
        super().setUp()
        self.adm_a, self.adm_b = usuario("adm_a", self.cliente, "Administrador"), usuario("adm_b", self.outro, "Administrador")  # excluir é do Administrador

    def nova_funcao(self, cliente=None, nome="Cozinheiro", salario=220000, personalizavel=False, inicio=INICIO_ANTIGO):
        funcao = FuncaoMaoDeObra.objects.create(cliente=cliente or self.cliente, nome=nome)
        ComponenteFuncao.objects.create(funcao=funcao, categoria=custos.SALARIO, nome="Salário base", valor=salario, inicio=inicio, personalizavel=personalizavel)
        return funcao

    def componente(self, funcao, **extra):
        dados = {"funcao": funcao, "categoria": custos.BENEFICIO, "nome": "Vale-refeição", "valor": 30000, "inicio": INICIO_ANTIGO}
        dados.update(extra)
        return ComponenteFuncao.objects.create(**dados)

    def editar_colaborador(self, colaborador, **extra):
        dados = {"nome": colaborador.nome, "funcao": "", "valor_mes": "", "valor_hora": "", "valor_dia": "", "ativo": "on", **extra}
        return self.client.post(reverse("colaborador-edit", args=[colaborador.pk]), dados)


class TelasFuncaoTests(FuncoesBase):
    def test_cadastra_funcao_e_item_pela_tela_e_audita(self):
        r = self.client.post(reverse("funcao-create"), {"nome": "  Auxiliar   de cozinha ", "cbo_codigo": "513205", "cbo_descricao": "Cozinheiro geral", "ativo": "on"})
        self.assertEqual(r.status_code, 302, r.context and r.context["form"].errors)
        funcao = FuncaoMaoDeObra.objects.get()
        self.assertRedirects(r, reverse("funcao-detalhe", args=[funcao.pk]))
        self.assertEqual((funcao.cliente_id, funcao.nome, funcao.cbo_codigo), (self.cliente.pk, "Auxiliar de cozinha", "513205"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="maodeobra", acao="criar", tabela="maodeobra_funcaomaodeobra", registro_id=str(funcao.pk)).exists())
        r = self.client.post(reverse("componente-create", args=[funcao.pk]), {
            "categoria": "salario", "nome": "Salário base", "tipo_valor": "fixo", "valor": "2.200,00", "periodicidade": "mensal", "inicio": "2026-01-01", "ativo": "on",
        })
        self.assertEqual(r.status_code, 302, r.context and r.context["form"].errors)
        self.assertEqual(ComponenteFuncao.objects.get().valor, 220000)
        pagina = self.client.get(reverse("funcao-detalhe", args=[funcao.pk]))
        self.assertContains(pagina, "R$ 2.200,00")

    def test_validacoes_da_tela(self):
        f = self.nova_funcao()
        self.assertContains(self.client.post(reverse("funcao-create"), {"nome": "cozinheiro", "ativo": "on"}), "Já existe uma função")
        self.assertContains(self.client.post(reverse("funcao-create"), {"nome": "Outra", "cbo_codigo": "12ab", "ativo": "on"}), "6 dígitos")
        base = {"nome": "Adic", "inicio": "2026-01-01", "ativo": "on"}
        r = self.client.post(reverse("componente-create", args=[f.pk]), {**base, "categoria": "adicional", "tipo_valor": "percentual", "percentual": "30", "base": "remuneracao"})
        self.assertContains(r, "Base inválida")
        r = self.client.post(reverse("componente-create", args=[f.pk]), {**base, "categoria": "salario", "tipo_valor": "fixo", "valor": "3.000,00", "periodicidade": "mensal"})
        self.assertContains(r, "Já existe um salário base vigente")
        r = self.client.post(reverse("componente-create", args=[f.pk]), {**base, "categoria": "encargo", "tipo_valor": "fixo", "valor": "10,00", "fim": "2025-01-01"})
        self.assertContains(r, "não pode ser anterior")
        self.assertEqual(ComponenteFuncao.objects.count(), 1)

    def test_salario_novo_com_vigencia_posterior_e_aceito(self):
        f = self.nova_funcao()
        antigo = ComponenteFuncao.objects.get()
        antigo.fim = date(2026, 6, 30)
        antigo.save()
        r = self.client.post(reverse("componente-create", args=[f.pk]), {"categoria": "salario", "nome": "Salário base", "tipo_valor": "fixo", "valor": "2.500,00", "inicio": "2026-07-01", "ativo": "on"})
        self.assertEqual(r.status_code, 302, r.context and r.context["form"].errors)
        self.assertEqual(composicao.composicao_da_funcao(f, date(2026, 6, 1)).composicao.total_mensal, 220000)
        self.assertEqual(composicao.composicao_da_funcao(f, date(2026, 7, 1)).composicao.total_mensal, 250000)

    def test_itens_vencidos_e_futuros_na_composicao_de_referencia(self):
        f = self.nova_funcao()
        self.componente(f, nome="Antigo", valor=10000, fim=date(2026, 3, 31))
        self.componente(f, nome="Futuro", valor=20000, inicio=date(2099, 1, 1))
        self.componente(f, nome="Inativo", valor=40000, ativo=False)
        self.assertEqual(composicao.composicao_da_funcao(f, date(2026, 2, 1)).composicao.total_mensal, 220000 + 10000)
        self.assertEqual(composicao.composicao_da_funcao(f, date(2026, 5, 1)).composicao.total_mensal, 220000)
        self.assertEqual(composicao.composicao_da_funcao(f, date(2099, 2, 1)).composicao.total_mensal, 220000 + 20000)

    def test_excluir_item_vigente_encerra_e_futuro_apaga(self):
        f = self.nova_funcao()
        vigente = self.componente(f, nome="Vigente", valor=10000)
        futuro = self.componente(f, nome="Futuro", valor=10000, inicio=date(2099, 1, 1))
        self.client.force_login(self.adm_a)
        self.client.post(reverse("componente-excluir", args=[vigente.pk]))
        vigente.refresh_from_db()
        self.assertEqual(vigente.fim, ONTEM)  # histórico preservado
        self.client.post(reverse("componente-excluir", args=[futuro.pk]))
        self.assertFalse(ComponenteFuncao.objects.filter(pk=futuro.pk).exists())

    def test_funcao_com_vinculo_so_desativa(self):
        f = self.nova_funcao()
        livre = FuncaoMaoDeObra.objects.create(cliente=self.cliente, nome="Livre")
        self.editar_colaborador(self.ana, funcao_vinculada=f.pk)
        self.client.force_login(self.adm_a)
        self.client.post(reverse("funcao-excluir", args=[f.pk]))
        self.client.post(reverse("funcao-excluir", args=[livre.pk]))
        f.refresh_from_db()
        self.assertFalse(f.ativo)
        self.assertFalse(FuncaoMaoDeObra.objects.filter(pk=livre.pk).exists())


class VinculoEPersonalizacaoTests(FuncoesBase):
    def test_vincular_traz_salario_e_mantem_horas_mes_coerentes(self):
        f = self.nova_funcao(salario=220000)
        r = self.editar_colaborador(self.ana, funcao_vinculada=f.pk, valor_mes="9.999,00")  # salário NÃO personalizável: vale o da função
        self.assertEqual(r.status_code, 302)
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.valor_mes, self.ana.valor_hora, self.ana.valor_dia, self.ana.funcao), (220000, 1000, 7333, "Cozinheiro"))  # 220 h/mês -> R$ 10,00/h
        tela = self.client.get(reverse("colaborador-edit", args=[self.ana.pk]))
        self.assertContains(tela, "220 h/mês")  # o bloco de h/mês continua na edição
        self.assertContains(tela, 'id="previaMes"')
        self.assertEqual(VinculoFuncao.objects.get().funcao_id, f.pk)

    def test_salario_personalizavel_aceita_valor_proprio(self):
        f = self.nova_funcao(salario=220000, personalizavel=True)
        self.editar_colaborador(self.ana, funcao_vinculada=f.pk, valor_mes="3.300,00")
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.valor_mes, self.ana.valor_hora), (330000, 1500))
        r = composicao.composicao_do_colaborador(self.ana, HOJE)
        self.assertEqual((r.composicao.itens[0].origem, r.composicao.itens[0].herdado_valor, r.composicao.total_mensal), ("personalizado", 220000, 330000))
        sem_valor = Colaborador.objects.create(cliente=self.cliente, nome="Beto", valor_hora=1)
        self.editar_colaborador(sem_valor, funcao_vinculada=f.pk)
        sem_valor.refresh_from_db()
        self.assertEqual(sem_valor.valor_mes, 220000)  # sem valor próprio, herda o da função

    def test_colaborador_sem_funcao_continua_funcionando(self):
        r = self.editar_colaborador(self.ana, valor_hora="25,00")
        self.assertEqual(r.status_code, 302)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.valor_hora, 2500)
        resultado = composicao.composicao_do_colaborador(self.ana, HOJE)
        self.assertTrue(resultado.incompleto)
        self.assertEqual(resultado.composicao.total_mensal, 0)  # sem valor mensal nem função: nada a custear

    def test_trocar_de_funcao_preserva_historico_e_nao_leva_personalizacao(self):
        antiga = self.nova_funcao(nome="Auxiliar", salario=200000)
        nova = self.nova_funcao(nome="Cozinheiro", salario=300000)
        vr = self.componente(antiga, personalizavel=True, valor=30000)
        self.editar_colaborador(self.ana, funcao_vinculada=antiga.pk, funcao_inicio="2026-01-01")
        vinculo = VinculoFuncao.objects.get()
        PersonalizacaoColaborador.objects.create(vinculo=vinculo, componente=vr, valor=45000)
        self.editar_colaborador(self.ana, funcao_vinculada=nova.pk, funcao_inicio="2026-06-01")
        antigos = list(VinculoFuncao.objects.order_by("inicio"))
        self.assertEqual([(v.funcao_id, v.inicio, v.fim) for v in antigos], [(antiga.pk, date(2026, 1, 1), date(2026, 5, 31)), (nova.pk, date(2026, 6, 1), None)])
        antes = composicao.composicao_do_colaborador(self.ana, date(2026, 3, 1))
        depois = composicao.composicao_do_colaborador(self.ana, date(2026, 7, 1))
        self.assertEqual((antes.funcao.pk, antes.composicao.total_mensal), (antiga.pk, 200000 + 45000))  # histórico com a personalização da época
        self.assertEqual((depois.funcao.pk, depois.composicao.total_mensal), (nova.pk, 300000))  # nada herdado da função antiga
        self.assertEqual(self.client.get(reverse("colaborador-composicao", args=[self.ana.pk])).status_code, 200)

    def test_troca_com_inicio_anterior_ao_vinculo_atual_e_recusada(self):
        a, b = self.nova_funcao(nome="A"), self.nova_funcao(nome="B")
        self.editar_colaborador(self.ana, funcao_vinculada=a.pk, funcao_inicio="2026-06-01")
        r = self.editar_colaborador(self.ana, funcao_vinculada=b.pk, funcao_inicio="2026-05-01")
        self.assertContains(r, "depois do início da função atual")
        self.assertEqual(VinculoFuncao.objects.count(), 1)

    def test_funcao_de_outra_unidade_e_recusada(self):
        alheia = self.nova_funcao(cliente=self.outro, nome="Alheia")
        r = self.editar_colaborador(self.ana, funcao_vinculada=alheia.pk)
        self.assertEqual(r.status_code, 200)  # o campo não oferece funções de fora do escopo
        self.assertFalse(VinculoFuncao.objects.exists())

    def test_personalizar_somente_itens_marcados(self):
        f = self.nova_funcao()
        livre = self.componente(f, nome="Livre", personalizavel=True, valor=10000)
        fixo = self.componente(f, nome="Fixo", valor=20000)
        pct = self.componente(f, nome="Plano", categoria=custos.BENEFICIO, tipo_valor=custos.PERCENTUAL, valor=None, percentual=D("2"), personalizavel=True)
        self.editar_colaborador(self.ana, funcao_vinculada=f.pk)
        url = reverse("colaborador-composicao", args=[self.ana.pk])
        pagina = self.client.get(url)
        self.assertContains(pagina, f"item_{livre.pk}")
        self.assertNotContains(pagina, f"item_{fixo.pk}")  # não personalizável não tem campo
        r = self.client.post(url, {f"item_{livre.pk}": "150,00", f"item_{pct.pk}": "3,5", f"item_{fixo.pk}": "1,00"})
        self.assertEqual(r.status_code, 302)
        p = {x.componente_id: x for x in PersonalizacaoColaborador.objects.all()}
        self.assertEqual((p[livre.pk].valor, p[pct.pk].percentual, fixo.pk in p), (15000, D("3.5"), False))
        r = composicao.composicao_do_colaborador(self.ana, HOJE)
        por_nome = {i.nome: i for i in r.composicao.itens}
        self.assertEqual((por_nome["Livre"].origem, por_nome["Livre"].herdado_valor, por_nome["Livre"].mensal), ("personalizado", 10000, 15000))
        self.assertEqual((por_nome["Fixo"].origem, por_nome["Fixo"].mensal), ("funcao", 20000))
        self.assertEqual(por_nome["Plano"].mensal, 7700)  # 3,5% de 220.000 (benefício sobre o salário)
        self.client.post(url, {f"item_{livre.pk}": "", f"item_{pct.pk}": ""})  # vazio volta a herdar
        self.assertFalse(PersonalizacaoColaborador.objects.exists())

    def test_alterar_item_da_funcao_nao_reescreve_personalizacao(self):
        f = self.nova_funcao()
        livre = self.componente(f, nome="Livre", personalizavel=True, valor=10000)
        self.editar_colaborador(self.ana, funcao_vinculada=f.pk)
        self.client.post(reverse("colaborador-composicao", args=[self.ana.pk]), {f"item_{livre.pk}": "150,00"})
        livre.valor = 12000
        livre.save()
        item_livre = [i for i in composicao.composicao_do_colaborador(self.ana, HOJE).composicao.itens if i.nome == "Livre"][0]
        self.assertEqual((item_livre.mensal, item_livre.herdado_valor), (15000, 12000))

    def test_operador_nao_personaliza(self):
        f = self.nova_funcao()
        self.editar_colaborador(self.ana, funcao_vinculada=f.pk)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("colaborador-composicao", args=[self.ana.pk])).status_code, 403)


class SimulacaoTests(FuncoesBase):
    def setUp(self):
        super().setUp()
        self.aux = self.nova_funcao(nome="Auxiliar", salario=200000)
        self.chefe = self.nova_funcao(nome="Chefe", salario=400000)
        self.componente(self.chefe, nome="Gratificação", categoria=custos.ADICIONAL, tipo_valor=custos.PERCENTUAL, valor=None, percentual=D("10"), personalizavel=True)
        self.editar_colaborador(self.ana, funcao_vinculada=self.aux.pk, funcao_inicio="2026-01-01")

    def criar(self, **extra):
        dados = {"nome": "Promoção da Ana", "tipo": "pessoa", "colaborador": self.ana.pk, "funcao_destino": self.chefe.pk, "data_referencia": HOJE.isoformat(), **extra}
        return self.client.post(reverse("simulacao-create"), dados)

    def test_promocao_por_pessoa_compara_sem_alterar_dados_reais(self):
        antes = (Colaborador.objects.values().get(pk=self.ana.pk), list(VinculoFuncao.objects.values()), list(ComponenteFuncao.objects.values_list("pk", "valor", "percentual")))
        r = self.criar()
        simulacao = SimulacaoPromocao.objects.get()
        self.assertRedirects(r, reverse("simulacao-detalhe", args=[simulacao.pk]))
        resultado = composicao.simular(simulacao)
        linha = resultado["linhas"][0]
        self.assertEqual((linha["atual"].composicao.total_mensal, linha["simulado"].total_mensal), (200000, 440000))  # 4.000 + 10% de adicional
        self.assertEqual((resultado["diferenca_mensal"], resultado["percentual"]), (240000, D("120.00")))
        mensal = [p for p in resultado["periodos"] if p["codigo"] == "mes"][0]
        self.assertEqual((mensal["atual"], mensal["simulado"], mensal["diferenca"]), (200000, 440000, 240000))
        anual = [p for p in resultado["periodos"] if p["codigo"] == "ano"][0]
        self.assertEqual(anual["diferenca"], 240000 * 12)
        depois = (Colaborador.objects.values().get(pk=self.ana.pk), list(VinculoFuncao.objects.values()), list(ComponenteFuncao.objects.values_list("pk", "valor", "percentual")))
        self.assertEqual(antes, depois)  # simulação nunca altera dados reais
        pagina = self.client.get(reverse("simulacao-detalhe", args=[simulacao.pk]))
        self.assertContains(pagina, "Planejamento")
        self.assertContains(pagina, "R$ 4.400,00")

    def test_salario_novo_e_ajuste_percentual(self):
        self.criar(nome="Aumento", funcao_destino="", ajuste_salario_percentual="10")
        sim = SimulacaoPromocao.objects.get()
        self.assertEqual(composicao.simular(sim)["linhas"][0]["simulado"].total_mensal, 220000)
        sim.ajuste_salario_percentual = None
        sim.novo_salario = 260000
        sim.save()
        self.assertEqual(composicao.simular(sim)["linhas"][0]["simulado"].total_mensal, 260000)
        r = self.criar(nome="Duplo", novo_salario="3.000,00", ajuste_salario_percentual="5")
        self.assertContains(r, "não os dois")

    def test_por_funcao_alcanca_so_colaboradores_ativos_da_funcao(self):
        outro = Colaborador.objects.create(cliente=self.cliente, nome="Caio", valor_hora=1)
        inativo = Colaborador.objects.create(cliente=self.cliente, nome="Dora", valor_hora=1, ativo=False)
        for c in (outro, inativo):
            VinculoFuncao.objects.create(colaborador=c, funcao=self.aux, inicio=INICIO_ANTIGO)
        self.client.post(reverse("simulacao-create"), {"nome": "Reajuste do auxiliar", "tipo": "funcao", "funcao_origem": self.aux.pk, "ajuste_salario_percentual": "10", "data_referencia": HOJE.isoformat()})
        resultado = composicao.simular(SimulacaoPromocao.objects.get())
        self.assertEqual(resultado["quantidade"], 2)  # Ana e Caio; a Dora está inativa
        self.assertEqual((resultado["total_mensal_atual"], resultado["total_mensal_simulado"]), (400000, 440000))

    def test_tipo_exige_o_alvo_e_unidade_certa(self):
        alheio = Colaborador.objects.create(cliente=self.outro, nome="Zé", valor_hora=1)
        r = self.criar(colaborador=alheio.pk)
        self.assertEqual(r.status_code, 200)  # o campo só oferece colaboradores do escopo
        self.assertFalse(SimulacaoPromocao.objects.exists())
        r = self.client.post(reverse("simulacao-create"), {"nome": "Sem alvo", "tipo": "funcao", "data_referencia": HOJE.isoformat()})
        self.assertContains(r, "função de origem")

    def test_isolamento_entre_unidades(self):
        self.criar()
        sim = SimulacaoPromocao.objects.get()
        self.client.force_login(self.adm_b)
        for nome in ("simulacao-detalhe", "simulacao-edit"):
            self.assertEqual(self.client.get(reverse(nome, args=[sim.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("simulacao-excluir", args=[sim.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("simulacao-list")), "Promoção da Ana")
        self.assertTrue(SimulacaoPromocao.objects.filter(pk=sim.pk).exists())

    def test_operador_nao_ve_simulacoes(self):
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("simulacao-list")).status_code, 403)

    def test_exclusao_da_simulacao_e_auditada(self):
        self.criar()
        sim = SimulacaoPromocao.objects.get()
        self.client.force_login(self.adm_a)
        self.client.post(reverse("simulacao-excluir", args=[sim.pk]))
        self.assertFalse(SimulacaoPromocao.objects.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="maodeobra", acao__icontains="exclu", tabela="maodeobra_simulacaopromocao").exists())


class DashboardTests(FuncoesBase):
    def setUp(self):
        super().setUp()
        self.aux = self.nova_funcao(nome="Auxiliar", salario=200000)
        self.componente(self.aux, nome="Vale", valor=20000)
        self.beto = Colaborador.objects.create(cliente=self.cliente, nome="Beto", valor_hora=1000)
        self.editar_colaborador(self.ana, funcao_vinculada=self.aux.pk, funcao_inicio="2026-01-01")
        self.editar_colaborador(self.beto, funcao_vinculada=self.aux.pk, funcao_inicio="2026-01-01")

    def test_indicadores_basicos(self):
        r = self.client.get(reverse("mao-obra-dashboard"))
        ind = r.context["ind"]
        self.assertEqual((ind["quantidade"], ind["total_mensal"], ind["medio_mensal"], ind["incompletos"]), (2, 440000, 220000, 0))
        self.assertEqual(ind["por_funcao"], [{"funcao": "Auxiliar", "quantidade": 2, "total_mensal": 440000, "medio_mensal": 220000}])
        self.assertEqual({c["codigo"]: c["valor"] for c in ind["categorias"]}[custos.BENEFICIO], 40000)
        self.assertContains(r, "R$ 4.400,00")

    def test_dados_incompletos_sao_avisados(self):
        Colaborador.objects.create(cliente=self.cliente, nome="Sem função", valor_hora=1000, valor_mes=150000)
        r = self.client.get(reverse("mao-obra-dashboard"))
        ind = r.context["ind"]
        self.assertEqual((ind["quantidade"], ind["incompletos"], ind["total_mensal"]), (3, 1, 440000 + 150000))
        self.assertContains(r, "Dados incompletos")
        self.assertIn("Sem função", [f["funcao"] for f in ind["por_funcao"]])

    def test_filtro_por_funcao_e_comparacao_entre_datas(self):
        outra = self.nova_funcao(nome="Chefe", salario=500000)
        chefe = Colaborador.objects.create(cliente=self.cliente, nome="Chefe", valor_hora=1)
        self.editar_colaborador(chefe, funcao_vinculada=outra.pk, funcao_inicio="2026-01-01")
        r = self.client.get(reverse("mao-obra-dashboard"), {"funcao": outra.pk})
        self.assertEqual(r.context["ind"]["total_mensal"], 500000)
        # aumento de salário vigente só hoje: compara com ontem
        antigo = ComponenteFuncao.objects.get(funcao=self.aux, categoria="salario")
        antigo.fim = ONTEM
        antigo.save()
        ComponenteFuncao.objects.create(funcao=self.aux, categoria="salario", nome="Salário base", valor=250000, inicio=HOJE)
        r = self.client.get(reverse("mao-obra-dashboard"), {"data": HOJE.isoformat(), "comparar_com": ONTEM.isoformat(), "funcao": self.aux.pk})
        comp = r.context["ind"]["comparacao"]
        self.assertEqual((comp["total_mensal"], comp["diferenca"], comp["percentual"]), (440000, 100000, D("22.73")))

    def test_periodo_e_filtros_invalidos_nao_quebram(self):
        self.assertEqual(self.client.get(reverse("mao-obra-dashboard"), {"data": "lixo", "periodo": "xyz", "funcao": "999999"}).status_code, 200)

    def test_nao_mostra_dados_de_outra_unidade(self):
        f_b = self.nova_funcao(cliente=self.outro, nome="Secreta B", salario=999999)
        zeca = Colaborador.objects.create(cliente=self.outro, nome="Zeca B", valor_hora=1)
        VinculoFuncao.objects.create(colaborador=zeca, funcao=f_b, inicio=INICIO_ANTIGO)
        r = self.client.get(reverse("mao-obra-dashboard"))
        self.assertEqual(r.context["ind"]["quantidade"], 2)
        self.assertNotContains(r, "Secreta B")
        fora = self.client.get(reverse("mao-obra-dashboard"), {"funcao": f_b.pk}).context["ind"]
        self.assertEqual((fora["quantidade"], fora["total_mensal"]), (2, 440000))  # função de fora do escopo: filtro inválido, nada de B aparece
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("mao-obra-dashboard")).context["ind"]["total_mensal"], 999999)

    def test_operador_e_segmento_sem_o_modulo_nao_acessam(self):
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("mao-obra-dashboard")).status_code, 403)
        self.client.force_login(self.gestor_padaria)
        self.assertEqual(self.client.get(reverse("mao-obra-dashboard")).status_code, 403)

    def test_h_mes_continua_na_lista_de_colaboradores(self):
        self.ana.refresh_from_db()
        lista = self.client.get(reverse("colaborador-list"))
        self.assertContains(lista, "Quinzena")
        self.assertEqual(self.ana.valor_hora, 909)  # R$ 2.000,00 ÷ 220 h/mês = R$ 9,09
        self.assertContains(lista, "R$ 9,09")


class TenantEPermissaoTests(FuncoesBase):
    def test_outra_unidade_recebe_404_em_funcao_e_itens(self):
        f = self.nova_funcao()
        item_a = self.componente(f)
        self.client.force_login(self.adm_b)
        for nome, args in (("funcao-detalhe", [f.pk]), ("funcao-edit", [f.pk]), ("componente-create", [f.pk]), ("componente-edit", [item_a.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 404, nome)
        for nome, args in (("funcao-excluir", [f.pk]), ("componente-excluir", [item_a.pk]), ("componente-create", [f.pk]), ("funcao-edit", [f.pk])):
            self.assertEqual(self.client.post(reverse(nome, args=args), {"nome": "X"}).status_code, 404, nome)
        self.assertEqual(self.client.get(reverse("colaborador-composicao", args=[self.ana.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("funcao-list")), "Cozinheiro")
        f.refresh_from_db()
        self.assertTrue(f.ativo)

    def test_operador_e_visualizador_nao_veem_custos(self):
        f = self.nova_funcao()
        self.client.force_login(self.operador)
        for nome, args in (("funcao-list", []), ("funcao-detalhe", [f.pk]), ("funcao-create", []), ("colaborador-composicao", [self.ana.pk]), ("mao-obra-dashboard", [])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 403, nome)

    def test_cliente_da_funcao_vem_do_servidor_e_nao_do_corpo(self):
        self.client.post(reverse("funcao-create"), {"nome": "Intrusa", "cliente": self.outro.pk, "ativo": "on"})
        self.assertEqual(FuncaoMaoDeObra.objects.get(nome="Intrusa").cliente_id, self.cliente.pk)

    def test_gestor_nao_exclui_item_so_administrador(self):
        f = self.nova_funcao()
        item_a = self.componente(f)
        resposta = self.client.post(reverse("componente-excluir", args=[item_a.pk]))
        self.assertEqual(resposta.status_code, 403)  # excluir é do Administrador
        item_a.refresh_from_db()
        self.assertIsNone(item_a.fim)

    def test_menu_mostra_abas_novas(self):
        pagina = self.client.get(reverse("colaborador-list"))
        for rotulo in ("Funções", "Simulações", "Dashboard"):
            self.assertContains(pagina, rotulo)

    def test_tour_das_telas_novas(self):
        f = self.nova_funcao()
        telas = [
            (reverse("funcao-list"), "funcoes"), (reverse("funcao-create"), "funcao-form"), (reverse("funcao-detalhe", args=[f.pk]), "funcao-detalhe"),
            (reverse("componente-create", args=[f.pk]), "componente-form"), (reverse("colaborador-composicao", args=[self.ana.pk]), "colaborador-composicao"),
            (reverse("simulacao-list"), "simulacoes"), (reverse("simulacao-create"), "simulacao-form"), (reverse("mao-obra-dashboard"), "mao-obra-dashboard"),
            (reverse("colaborador-edit", args=[self.ana.pk]), "colaborador-form"),
        ]
        for url, chave in telas:
            self.assertContains(self.client.get(url), f"initTourModulo('{chave}')", msg_prefix=url)


class ApiFuncoesTests(FuncoesBase):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o, self.api_b = (self.api(u) for u in (self.gestor, self.operador, self.gestor_b))

    def api(self, usuario_):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario_)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def ok(self, r, codigo=200):
        self.assertEqual(r.status_code, codigo, r.content[:500])
        return r.json().get("data")

    def test_fluxo_completo_funcao_itens_vinculo_composicao(self):
        f = self.ok(self.api_g.post(f"{V1}/mao-de-obra/funcoes/", {"nome": "Instalador", "cbo_codigo": "715210", "cliente": 999}, format="json"), 201)
        self.assertEqual(FuncaoMaoDeObra.objects.get(pk=f["id"]).cliente_id, self.cliente.pk)  # tenant do usuário, nunca do corpo
        sal = self.ok(self.api_g.post(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": f["id"], "categoria": "salario", "nome": "Salário", "valor": 330000, "inicio": "2026-01-01", "personalizavel": True}, format="json"), 201)
        enc = self.ok(self.api_g.post(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": f["id"], "categoria": "encargo", "nome": "Encargo X", "tipo_valor": "percentual", "percentual": "20", "base": "salario", "inicio": "2026-01-01"}, format="json"), 201)
        vr = self.ok(self.api_g.post(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": f["id"], "categoria": "beneficio", "nome": "VR", "valor": 40000, "inicio": "2026-01-01", "personalizavel": True}, format="json"), 201)
        self.assertEqual((sal["valor"], enc["percentual"], vr["personalizavel"]), (330000, "20.0000", True))
        col = self.ok(self.api_g.post(f"{V1}/colaboradores/", {"nome": "Duda", "valor_hora": 100, "funcao_vinculada": f["id"], "funcao_inicio": "2026-01-01"}, format="json"), 201)
        self.assertEqual((col["funcao_vinculada"], col["funcao_vinculada_nome"], col["valor_mes"], col["valor_hora"]), (f["id"], "Instalador", 330000, 1500))  # 330.000 ÷ 220 h/mês
        comp = self.ok(self.api_g.get(f"{V1}/colaboradores/{col['id']}/composicao/", {"data": "2026-10-01"}))
        self.assertEqual((comp["total_mensal"], comp["periodos"]["mes"], comp["periodos"]["ano"], comp["periodos"]["dia"]), (330000 + 66000 + 40000, 436000, 436000 * 12, 14533))
        self.assertEqual(comp["por_categoria"]["encargo"], 66000)
        antes = self.ok(self.api_g.get(f"{V1}/colaboradores/{col['id']}/composicao/", {"data": "2025-12-31"}))
        self.assertEqual((antes["total_mensal"], antes["incompleto"], antes["funcao"]), (330000, True, None))  # antes do vínculo: só o salário do cadastro
        # personalização: só itens personalizáveis; o encargo (não marcado) é recusado
        r = self.api_g.put(f"{V1}/colaboradores/{col['id']}/personalizacoes/", {"itens": [{"componente": enc["id"], "percentual": "30"}]}, format="json")
        self.assertEqual(r.status_code, 400)
        novo = self.ok(self.api_g.put(f"{V1}/colaboradores/{col['id']}/personalizacoes/", {"itens": [{"componente": vr["id"], "valor": 55000}]}, format="json"))
        vr_item = [i for i in novo["itens"] if i["nome"] == "VR"][0]
        self.assertEqual((vr_item["origem"], vr_item["valor_herdado"], vr_item["valor_mensal"], novo["total_mensal"]), ("personalizado", 40000, 55000, 330000 + 66000 + 55000))
        zerado = self.ok(self.api_g.put(f"{V1}/colaboradores/{col['id']}/personalizacoes/", {"itens": []}, format="json"))
        self.assertEqual(zerado["total_mensal"], 436000)
        # o colaborador sem função cadastrada continua listado com campos nulos
        self.ok(self.api_g.post(f"{V1}/colaboradores/", {"nome": "Sem função", "valor_hora": 2000}, format="json"), 201)
        nomes = {c["nome"]: c["funcao_vinculada"] for c in self.ok(self.api_g.get(f"{V1}/colaboradores/"))["itens"]}
        self.assertEqual((nomes["Duda"], nomes["Sem função"]), (f["id"], None))
        # patch de outro campo não desfaz a coerência do salário com a função
        self.ok(self.api_g.patch(f"{V1}/colaboradores/{col['id']}/", {"valor_mes": 100000}, format="json"))  # salário é personalizável: aceita
        self.assertEqual(Colaborador.objects.get(pk=col["id"]).valor_mes, 100000)

    def test_validacao_e_isolamento_da_api(self):
        f = self.nova_funcao()
        alheia = self.nova_funcao(cliente=self.outro, nome="Alheia")
        r = self.api_g.post(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": alheia.pk, "categoria": "beneficio", "nome": "X", "valor": 1, "inicio": "2026-01-01"}, format="json")
        self.assertEqual(r.status_code, 400)  # função de outra unidade
        r = self.api_g.post(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": f.pk, "categoria": "adicional", "nome": "X", "tipo_valor": "percentual", "percentual": "5", "base": "remuneracao", "inicio": "2026-01-01"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_b.get(f"{V1}/mao-de-obra/funcoes/{f.pk}/").status_code, 404)
        self.assertEqual(self.api_b.patch(f"{V1}/mao-de-obra/funcoes/{f.pk}/", {"nome": "Hack"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.adm_b).delete(f"{V1}/mao-de-obra/funcoes/{f.pk}/").status_code, 404)
        item_a = ComponenteFuncao.objects.get(funcao=f)
        self.assertEqual(self.api_b.get(f"{V1}/mao-de-obra/itens-de-custo/{item_a.pk}/").status_code, 404)
        self.assertEqual(self.ok(self.api_b.get(f"{V1}/mao-de-obra/itens-de-custo/", {"funcao": f.pk}))["itens"], [])
        self.assertEqual(self.api_b.get(f"{V1}/colaboradores/{self.ana.pk}/composicao/").status_code, 404)
        self.assertEqual(self.api_b.put(f"{V1}/colaboradores/{self.ana.pk}/personalizacoes/", {"itens": []}, format="json").status_code, 404)
        self.assertEqual(self.api_g.get(f"{V1}/colaboradores/{self.ana.pk}/composicao/", {"data": "31/12/2026"}).status_code, 400)

    def test_operador_nao_acessa_custos_pela_api(self):
        f = self.nova_funcao()
        for caminho in ("mao-de-obra/funcoes/", f"mao-de-obra/funcoes/{f.pk}/", "mao-de-obra/itens-de-custo/", "mao-de-obra/simulacoes/", "mao-de-obra/dashboard/", f"colaboradores/{self.ana.pk}/composicao/"):
            self.assertEqual(self.api_o.get(f"{V1}/{caminho}").status_code, 403, caminho)
        self.assertEqual(self.api_o.post(f"{V1}/mao-de-obra/funcoes/", {"nome": "X"}, format="json").status_code, 403)
        self.assertEqual(self.api_g.delete(f"{V1}/mao-de-obra/funcoes/{f.pk}/").status_code, 403)  # excluir é do Administrador

    def test_exclusao_de_item_pela_api_encerra_o_vigente(self):
        f = self.nova_funcao()
        item_a = self.componente(f)
        self.assertEqual(self.api(self.adm_a).delete(f"{V1}/mao-de-obra/itens-de-custo/{item_a.pk}/").status_code, 204)
        item_a.refresh_from_db()
        self.assertEqual(item_a.fim, ONTEM)

    def test_simulacao_e_dashboard_pela_api(self):
        aux, chefe = self.nova_funcao(nome="Auxiliar", salario=200000), self.nova_funcao(nome="Chefe", salario=400000)
        self.editar_colaborador(self.ana, funcao_vinculada=aux.pk, funcao_inicio="2026-01-01")
        s = self.ok(self.api_g.post(f"{V1}/mao-de-obra/simulacoes/", {"nome": "Promover Ana", "tipo": "pessoa", "colaborador": self.ana.pk, "funcao_destino": chefe.pk, "data_referencia": HOJE.isoformat()}, format="json"), 201)
        resultado = self.ok(self.api_g.get(f"{V1}/mao-de-obra/simulacoes/{s['id']}/resultado/"))
        self.assertEqual((resultado["quantidade"], resultado["total_mensal_atual"], resultado["total_mensal_simulado"], resultado["diferenca_mensal"], resultado["percentual"]), (1, 200000, 400000, 200000, "100.00"))
        self.assertEqual([p["codigo"] for p in resultado["periodos"]], [c for c, _ in custos.PERIODOS])
        r = self.api_g.post(f"{V1}/mao-de-obra/simulacoes/", {"nome": "Inválida", "tipo": "pessoa", "data_referencia": HOJE.isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api_g.post(f"{V1}/mao-de-obra/simulacoes/", {"nome": "Alheia", "tipo": "pessoa", "colaborador": Colaborador.objects.create(cliente=self.outro, nome="B", valor_hora=1).pk, "data_referencia": HOJE.isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_b.get(f"{V1}/mao-de-obra/simulacoes/{s['id']}/resultado/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/mao-de-obra/simulacoes/{s['id']}/").status_code, 404)
        dash = self.ok(self.api_g.get(f"{V1}/mao-de-obra/dashboard/", {"comparar_com": "2025-01-01"}))
        self.assertEqual((dash["quantidade"], dash["total_mensal"], dash["incompletos"], dash["cenarios"][0]["nome"]), (1, 200000, 0, "Promover Ana"))
        self.assertEqual(dash["comparacao"]["total_mensal"], 200000)  # em 2025 ainda não havia vínculo: vale só o salário do cadastro
        dash_b = self.ok(self.api_b.get(f"{V1}/mao-de-obra/dashboard/"))  # só o colaborador "B" criado acima: nada da unidade A
        self.assertEqual((dash_b["quantidade"], dash_b["total_mensal"], dash_b["cenarios"]), (1, 0, []))
        self.assertEqual(self.api_g.get(f"{V1}/mao-de-obra/dashboard/", {"data": "x"}).status_code, 400)
        self.assertEqual(self.api_g.delete(f"{V1}/mao-de-obra/simulacoes/{s['id']}/").status_code, 403)  # excluir é do Administrador
        self.assertEqual(self.api(self.adm_a).delete(f"{V1}/mao-de-obra/simulacoes/{s['id']}/").status_code, 204)
        self.assertFalse(SimulacaoPromocao.objects.exists())

    def test_swagger_documenta_os_endpoints_novos(self):
        admin = User.objects.create_superuser("adm2", "adm2@t.local", "SenhaForte123!")
        self.client.force_login(admin)
        schema = self.client.get("/api/schema/", {"format": "json"}).json()["paths"]
        for caminho in (
            "/api/v1/mao-de-obra/funcoes/", "/api/v1/mao-de-obra/funcoes/{id}/", "/api/v1/mao-de-obra/itens-de-custo/", "/api/v1/mao-de-obra/itens-de-custo/{id}/",
            "/api/v1/mao-de-obra/simulacoes/", "/api/v1/mao-de-obra/simulacoes/{id}/resultado/", "/api/v1/mao-de-obra/dashboard/",
            "/api/v1/colaboradores/{id}/composicao/", "/api/v1/colaboradores/{id}/personalizacoes/",
        ):
            self.assertIn(caminho, schema)
        self.assertIn("post", schema["/api/v1/mao-de-obra/itens-de-custo/"])
        self.assertIn("put", schema["/api/v1/colaboradores/{id}/personalizacoes/"])
