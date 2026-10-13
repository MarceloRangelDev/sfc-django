from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import recalculo
from .models import Processado, ProcessadoItem
from .recalculo import CicloDeDependenciaError, custo_do_item, propagar_insumos
from .services import excluir_processado, mensagem_insumo_em_uso, salvar_processado


def novo_cliente(nome, cnpj):
    cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome)
    habilitar_modulo(cliente, "insumo")
    habilitar_modulo(cliente, "processado")
    return cliente


def criar(cliente, nome, itens, rendimento="500", sigla="g"):
    """Cria um processado pelo serviço real. itens: [(insumo, sigla, quantidade)]."""
    processado = Processado(
        cliente=cliente, nome=nome, rendimento_quantidade=Decimal(rendimento), rendimento_medida=medida(sigla)
    )
    linhas = [ProcessadoItem(insumo=i, medida=medida(s), quantidade=Decimal(q)) for i, s, q in itens]
    salvar_processado(None, processado, linhas)
    processado.refresh_from_db()
    return processado


class CalculoTests(TestCase):
    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")

    def test_exemplo_do_camarao(self):
        """1 kg a R$ 39,90 rende 500 g limpos -> R$ 79,80 por kg, fator 0,5."""
        camarao = novo_insumo(self.cliente, "Camarão", "kg", "1", 3990)
        limpo = criar(self.cliente, "Camarão limpo", [(camarao, "kg", "1")], "500", "g")
        self.assertEqual(limpo.custo_total, 3990)
        self.assertEqual(limpo.custo_unitario, 7980)
        self.assertEqual(limpo.fator_rendimento, Decimal("0.5000"))
        self.assertEqual(limpo.reducao_percentual, Decimal("50.00"))
        self.assertEqual(limpo.insumo.preco_unidade, 7980)  # aparece na lista de insumos a R$ 79,80/kg
        self.assertEqual(limpo.insumo.nome, "Camarão limpo")

    def test_conversao_de_unidades_no_item(self):
        farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 500)  # R$ 5,00 o kg
        self.assertEqual(custo_do_item(farinha, medida("g"), Decimal("250")), 125)

    def test_unidades_incompativeis_sao_recusadas(self):
        leite = novo_insumo(self.cliente, "Leite", "L", "1", 500)
        with self.assertRaises(RegraNegocioError):
            custo_do_item(leite, medida("g"), Decimal("100"))

    def test_fator_ausente_quando_dimensoes_diferem(self):
        leite = novo_insumo(self.cliente, "Leite", "L", "1", 500)
        doce = criar(self.cliente, "Doce", [(leite, "L", "1")], "400", "g")  # litro vira kg: sem fator honesto
        self.assertIsNone(doce.fator_rendimento)
        self.assertEqual(doce.custo_total, 500)

    def test_nome_do_processado_nao_colide_com_insumo(self):
        from .forms import ProcessadoForm

        novo_insumo(self.cliente, "Molho")
        form = ProcessadoForm({"nome": "molho", "rendimento_quantidade": "1", "rendimento_medida": medida("kg").pk}, cliente=self.cliente)
        self.assertFalse(form.is_valid())
        self.assertIn("nome", form.errors)

    def test_saida_aceita_massa_volume_unidade_e_comprimento_mas_nao_area_ou_tempo(self):
        from .forms import ProcessadoForm

        for sigla, valida in (("g", True), ("kg", True), ("ml", True), ("L", True), ("un", True), ("m", True), ("m²", False), ("s", False)):
            form = ProcessadoForm({"nome": "X", "rendimento_quantidade": "1", "rendimento_medida": medida(sigla).pk}, cliente=self.cliente)
            self.assertEqual(form.is_valid(), valida, sigla)


class RecalculoEmCascataTests(TestCase):
    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")
        self.a = novo_insumo(self.cliente, "A", "kg", "1", 1000)  # R$ 10,00 / kg
        self.b = criar(self.cliente, "B", [(self.a, "kg", "1")], "1", "kg")  # B = A
        self.c = criar(self.cliente, "C", [(self.b.insumo, "kg", "2")], "1", "kg")  # C = 2 x B

    def custo(self, p):
        p.refresh_from_db()
        return p.custo_total

    def test_alterar_insumo_propaga_por_todos_os_niveis(self):
        self.assertEqual((self.custo(self.b), self.custo(self.c)), (1000, 2000))
        self.a.preco_pago = 1500
        self.a.save()  # sinal post_save -> motor
        self.assertEqual((self.custo(self.b), self.custo(self.c)), (1500, 3000))
        self.c.insumo.refresh_from_db()
        self.assertEqual(self.c.insumo.preco_pago, 3000)
        self.assertEqual(ProcessadoItem.objects.get(processado=self.c).custo, 3000)

    def test_mudar_unidade_do_insumo_recalcula(self):
        self.a.quantidade = Decimal("2")  # mesmos R$ 10,00 agora por 2 kg
        self.a.save()
        self.assertEqual((self.custo(self.b), self.custo(self.c)), (500, 1000))

    def test_dependencia_compartilhada_recalcula_cada_registro_uma_vez(self):
        d = criar(self.cliente, "D", [(self.b.insumo, "kg", "1"), (self.c.insumo, "kg", "1")], "1", "kg")  # losango
        original = recalculo.recalcular_processado
        with mock.patch.object(recalculo, "recalcular_processado", side_effect=original) as espiao:
            self.a.preco_pago = 2000
            self.a.save()
        chamados = [c.args[0].pk for c in espiao.call_args_list]
        self.assertEqual(sorted(chamados), sorted({self.b.pk, self.c.pk, d.pk}))
        self.assertEqual(len(chamados), len(set(chamados)))
        self.assertEqual(self.custo(d), 2000 + 4000)

    def test_ciclo_e_recusado_ao_salvar(self):
        # B passaria a usar C, que usa B.
        processado = Processado.objects.get(pk=self.b.pk)
        item = ProcessadoItem(insumo=self.c.insumo, medida=medida("kg"), quantidade=Decimal("1"))
        with self.assertRaises(ValidationError) as ctx:
            salvar_processado(None, processado, [item])
        self.assertIn("circular", ctx.exception.messages[0])
        self.assertEqual(self.custo(self.b), 1000)  # nada foi gravado

    def test_ciclo_nos_dados_e_detectado_no_motor_sem_recursao_infinita(self):
        # Corrompe os dados de propósito (por fora do serviço): B usa o insumo de C.
        ProcessadoItem.objects.create(processado=self.b, insumo=self.c.insumo, medida=medida("kg"), quantidade=Decimal("1"))
        with self.assertRaises(CicloDeDependenciaError):
            propagar_insumos([self.a.pk], self.cliente.pk)

    def test_falha_desfaz_tudo(self):
        with mock.patch.object(recalculo, "recalcular_processado", side_effect=[None, RuntimeError("boom")]):
            self.a.preco_pago = 9999
            with self.assertRaises(RuntimeError):
                self.a.save()
        # O save do insumo já ocorreu fora de transação aqui, mas o motor não deixa
        # dependentes pela metade: o comando de nova tentativa os acerta.
        call_command("recalcular_custos", verbosity=0)
        self.assertEqual((self.custo(self.b), self.custo(self.c)), (9999, 19998))

    def test_recalculo_e_idempotente(self):
        antes = (self.custo(self.b), self.custo(self.c))
        call_command("recalcular_custos", verbosity=0)
        call_command("recalcular_custos", verbosity=0)
        self.assertEqual((self.custo(self.b), self.custo(self.c)), antes)
        self.assertEqual(Insumo.objects.filter(nome="B").count(), 1)  # sem insumos duplicados

    def test_isolamento_entre_clientes(self):
        outro = novo_cliente("Outro", "34028316000103")
        a2 = novo_insumo(outro, "A", "kg", "1", 1000)
        b2 = criar(outro, "B", [(a2, "kg", "1")], "1", "kg")
        self.a.preco_pago = 5000
        self.a.save()
        self.assertEqual(self.custo(b2), 1000)  # o outro cliente não foi tocado
        self.assertEqual(self.custo(self.b), 5000)


class ExclusaoTests(TestCase):
    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")
        self.a = novo_insumo(self.cliente, "A", "kg", "1", 1000)
        self.b = criar(self.cliente, "B", [(self.a, "kg", "1")], "1", "kg")

    def test_insumo_em_uso_nao_pode_ser_excluido(self):
        self.assertIn("B", mensagem_insumo_em_uso(self.a))

    def test_excluir_processado_inativa_o_insumo_gerado(self):
        excluir_processado(None, self.b)
        self.b.refresh_from_db()
        self.b.insumo.refresh_from_db()
        self.assertFalse(self.b.ativo)
        self.assertFalse(self.b.insumo.ativo)
        self.assertIsNone(mensagem_insumo_em_uso(self.a))  # A fica livre

    def test_processado_usado_por_outro_nao_e_excluido(self):
        criar(self.cliente, "C", [(self.b.insumo, "kg", "1")], "1", "kg")
        with self.assertRaises(ValidationError):
            excluir_processado(None, self.b)


class TelasTests(TestCase):
    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")
        self.outro = novo_cliente("Outro", "34028316000103")
        grupo = Group.objects.create(name="G")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label__in=["insumo", "processado"]))
        self.user = User.objects.create_user(username="u", email="u@t.local", password="SenhaForte123!")
        self.user.profile.cliente = self.cliente
        self.user.profile.save()
        self.user.groups.add(grupo)
        self.client.force_login(self.user)
        self.a = novo_insumo(self.cliente, "Camarão", "kg", "1", 3990)

    def dados(self, **extra):
        base = {
            "nome": "Camarão limpo", "rendimento_quantidade": "500", "rendimento_medida": medida("g").pk, "ativo": "on",
            "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.a.pk, "itens-0-medida": medida("kg").pk, "itens-0-quantidade": "1",
        }
        return {**base, **extra}

    def test_criar_pela_tela_e_listar(self):
        resp = self.client.post(reverse("processado-create"), self.dados())
        self.assertEqual(resp.status_code, 302)
        p = Processado.objects.get(nome="Camarão limpo")
        self.assertEqual((p.cliente_id, p.custo_total), (self.cliente.pk, 3990))
        lista = self.client.get(reverse("processado"))
        self.assertContains(lista, "R$ 79,80")
        self.assertContains(self.client.get(reverse("insumo")), "Camarão limpo")  # na lista geral de insumos

    def test_editar_atualiza_e_ficha_vazia_e_recusada(self):
        self.client.post(reverse("processado-create"), self.dados())
        p = Processado.objects.get(nome="Camarão limpo")
        url = reverse("processado-edit", args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        vazio = self.dados(**{"itens-0-insumo": "", "itens-0-medida": "", "itens-0-quantidade": ""})
        self.assertEqual(self.client.post(url, vazio).status_code, 200)  # reexibe com erro
        resp = self.client.post(url, self.dados(rendimento_quantidade="250"))
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.insumo.preco_unidade, 15960)

    def test_insumo_de_outro_cliente_e_recusado(self):
        alheio = novo_insumo(self.outro, "Alheio")
        resp = self.client.post(reverse("processado-create"), self.dados(**{"itens-0-insumo": alheio.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Processado.objects.exists())

    def test_processado_de_outro_cliente_da_404(self):
        alheio = criar(self.outro, "Alheio", [(novo_insumo(self.outro, "X", "kg", "1", 100), "kg", "1")], "1", "kg")
        self.assertEqual(self.client.get(reverse("processado-edit", args=[alheio.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("processado-excluir", args=[alheio.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("processado")), "Alheio")

    def test_sem_permissao_e_negado(self):
        sem = User.objects.create_user(username="s", email="s@t.local", password="SenhaForte123!")
        sem.profile.cliente = self.cliente
        sem.profile.save()
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("processado")).status_code, 403)

    def test_insumo_gerado_nao_edita_nem_exclui_pela_tela_de_insumos(self):
        self.client.post(reverse("processado-create"), self.dados())
        gerado = Processado.objects.get().insumo
        self.assertEqual(self.client.get(reverse("insumo-edit", args=[gerado.pk])).status_code, 302)
        self.client.post(reverse("insumo-excluir", args=[gerado.pk]))
        gerado.refresh_from_db()
        self.assertTrue(gerado.ativo)

    def test_insumo_em_uso_nao_exclui_pela_tela(self):
        self.client.post(reverse("processado-create"), self.dados())
        self.client.post(reverse("insumo-excluir", args=[self.a.pk]))
        self.a.refresh_from_db()
        self.assertTrue(self.a.ativo)

    def test_salvar_e_adicionar_outro_no_insumo(self):
        dados = {"nome": "Novo", "medida": medida("un").pk, "quantidade": "1", "preco_pago": "1,00", "ativo": "on"}
        resp = self.client.post(reverse("insumo-create"), {**dados, "salvar_e_novo": "1"})
        self.assertRedirects(resp, reverse("insumo-create"), fetch_redirect_response=False)
        resp = self.client.post(reverse("insumo-create"), {**dados, "nome": "Outro"})
        self.assertRedirects(resp, reverse("insumo"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("insumo-create")), "name=\"salvar_e_novo\"")
        self.assertNotContains(self.client.get(reverse("insumo-edit", args=[self.a.pk])), "name=\"salvar_e_novo\"")

    def test_edicao_de_insumo_pela_tela_recalcula_dependentes(self):
        self.client.post(reverse("processado-create"), self.dados())
        dados = {"nome": "Camarão", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": "50,00", "ativo": "on"}
        self.assertEqual(self.client.post(reverse("insumo-edit", args=[self.a.pk]), dados).status_code, 302)
        self.assertEqual(Processado.objects.get().custo_total, 5000)

    def test_insumo_nao_troca_de_dimensao_se_em_uso(self):
        self.client.post(reverse("processado-create"), self.dados())
        dados = {"nome": "Camarão", "medida": medida("L").pk, "quantidade": "1", "preco_pago": "39,90", "ativo": "on"}
        self.assertEqual(self.client.post(reverse("insumo-edit", args=[self.a.pk]), dados).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.medida.sigla, medida("kg").sigla)


class InsumoGeradoMaiorGrandezaTests(TestCase):
    """O insumo gerado é sempre 1 unidade da maior grandeza (1 kg, 1 L, 1 un, 1 m) com o
    custo dessa unidade — sem "valor pago" de receita na lista de insumos."""

    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")

    def gerado(self, nome, itens, rendimento, sigla):
        return criar(self.cliente, nome, itens, rendimento, sigla).insumo

    def test_massa_em_gramas_vira_1_kg_com_o_custo_do_quilo(self):
        camarao = novo_insumo(self.cliente, "Camarão", "kg", "1", 3990)
        insumo = self.gerado("Camarão limpo", [(camarao, "kg", "1")], "500", "g")
        self.assertEqual((insumo.quantidade, insumo.medida.sigla, insumo.preco_pago, insumo.preco_unidade), (Decimal("1"), "kg", 7980, 7980))
        self.assertEqual(insumo.quantidade_por_extenso, "1 quilo")

    def test_volume_em_ml_vira_1_litro(self):
        leite = novo_insumo(self.cliente, "Leite", "L", "1", 600)  # R$ 6,00 o litro
        insumo = self.gerado("Creme", [(leite, "L", "1")], "250", "ml")  # R$ 6,00 por 250 ml = R$ 24,00 por litro
        self.assertEqual((insumo.quantidade, insumo.medida.sigla, insumo.preco_pago), (Decimal("1"), "L", 2400))

    def test_unidade_e_comprimento(self):
        massa = novo_insumo(self.cliente, "Massa", "kg", "1", 1200)
        pao = self.gerado("Pão", [(massa, "kg", "1")], "12", "un")  # 12 pães custaram R$ 12,00 -> R$ 1,00 por unidade
        self.assertEqual((pao.quantidade, pao.medida.sigla, pao.preco_pago), (Decimal("1"), "un", 100))
        fita = self.gerado("Fita", [(massa, "g", "500")], "200", "cm")  # R$ 6,00 por 2 m -> R$ 3,00 por metro
        self.assertEqual((fita.quantidade, fita.medida.sigla, fita.preco_pago), (Decimal("1"), "m", 300))

    def test_normalizacao_pos_migrate_converte_insumo_no_formato_antigo(self):
        from apps.processado.recalculo import insumo_gerado_desatualizado, normalizar_insumos_gerados

        camarao = novo_insumo(self.cliente, "Camarão", "kg", "1", 3990)
        processado = criar(self.cliente, "Camarão limpo", [(camarao, "kg", "1")], "500", "g")
        insumo = processado.insumo
        # simula o formato antigo: 500 g pelo valor total da receita
        type(insumo).objects.filter(pk=insumo.pk).update(quantidade=Decimal("500"), medida=processado.rendimento_medida, preco_pago=3990)
        processado.refresh_from_db()
        self.assertTrue(insumo_gerado_desatualizado(processado))
        self.assertEqual(normalizar_insumos_gerados(), [self.cliente.pk])
        insumo.refresh_from_db()
        self.assertEqual((insumo.quantidade, insumo.medida.sigla, insumo.preco_pago), (Decimal("1"), "kg", 7980))
        self.assertEqual(normalizar_insumos_gerados(), [])  # idempotente

    def test_saida_ja_em_kg_continua_1_kg(self):
        a = novo_insumo(self.cliente, "A", "kg", "1", 1000)
        insumo = self.gerado("B", [(a, "kg", "2")], "1", "kg")  # R$ 20,00 por kg
        self.assertEqual((insumo.quantidade, insumo.medida.sigla, insumo.preco_pago), (Decimal("1"), "kg", 2000))

    def test_cascata_continua_correta_com_o_insumo_de_referencia(self):
        a = novo_insumo(self.cliente, "A", "kg", "1", 1000)
        b = criar(self.cliente, "B", [(a, "kg", "1")], "500", "g")  # R$ 10,00 por 500 g = R$ 20,00/kg
        c = criar(self.cliente, "C", [(b.insumo, "g", "250")], "1", "kg")  # 250 g de B = R$ 5,00
        self.assertEqual(c.custo_total, 500)
        a.preco_pago = 2000
        a.save()
        b.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((b.insumo.preco_pago, c.custo_total), (4000, 1000))

    def test_insumo_com_estoque_mantem_a_unidade_do_saldo(self):
        from apps.estoque.services import registrar_entrada, saldo_do_insumo
        from django.contrib.auth.models import User

        a = novo_insumo(self.cliente, "A", "kg", "1", 1000)
        processado = criar(self.cliente, "B", [(a, "kg", "1")], "500", "g")
        insumo = processado.insumo
        usuario = User.objects.create_user(username="u", email="u@t.local", password="SenhaForte123!")
        # estoque lançado quando o insumo gerado ainda estava em gramas (formato antigo)
        Insumo.objects.filter(pk=insumo.pk).update(medida=medida("g"), quantidade=Decimal("500"), preco_pago=1000)
        insumo.refresh_from_db()
        registrar_entrada(usuario, insumo, Decimal("2000"), medida("g"))
        self.assertEqual(saldo_do_insumo(insumo), Decimal("2000"))
        call_command("recalcular_custos", verbosity=0)
        insumo.refresh_from_db()
        self.assertEqual((insumo.medida.sigla, insumo.quantidade, insumo.preco_pago), ("g", Decimal("1000"), 2000))  # 1 kg = 1000 g a R$ 20,00
        self.assertEqual(saldo_do_insumo(insumo), Decimal("2000"))  # o saldo não mudou de significado
        self.assertEqual(insumo.preco_unidade, 2000)  # R$ 20,00 por kg

    def test_lista_de_insumos_sem_prefixo_e_sem_valor_pago_do_processado(self):
        from django.contrib.auth.models import Group, Permission, User

        a = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        criar(self.cliente, "Massa pronta", [(a, "kg", "1")], "500", "g")
        grupo = Group.objects.create(name="g")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="insumo"))
        usuario = User.objects.create_user(username="v", email="v@t.local", password="SenhaForte123!")
        usuario.profile.cliente = self.cliente
        usuario.profile.save()
        usuario.groups.add(grupo)
        self.client.force_login(usuario)
        resp = self.client.get(reverse("insumo"))
        html = resp.content.decode()
        self.assertNotIn("INS-", html)  # só o nome do insumo, sem o código
        self.assertContains(resp, "Farinha")
        linha = next(l for l in html.split("<tr>") if "Massa pronta" in l)
        self.assertIn("R$ 20,00", linha)  # coluna Preço: custo do quilo
        self.assertNotIn("R$ 10,00", linha)  # nenhum "valor pago" da receita
        self.assertNotIn("Valor pago", html)  # a coluna deixou de existir


class ProcessadoFormularioTests(TestCase):
    """Salvar e adicionar outro, custo da ficha técnica em tempo real, valor por extenso e unidades com nome completo."""

    def setUp(self):
        self.cliente = novo_cliente("Empresa", "11222333000181")
        self.outro = novo_cliente("Outro", "34028316000103")
        grupo = Group.objects.create(name="G")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label__in=["insumo", "processado"]))
        self.user = User.objects.create_user(username="u", email="u@t.local", password="SenhaForte123!")
        self.user.profile.cliente = self.cliente
        self.user.profile.save()
        self.user.groups.add(grupo)
        self.client.force_login(self.user)
        self.a = novo_insumo(self.cliente, "Camarão", "kg", "1", 3990)

    def dados(self, **extra):
        base = {
            "nome": "Camarão limpo", "rendimento_quantidade": "500", "rendimento_medida": medida("g").pk, "ativo": "on",
            "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.a.pk, "itens-0-medida": medida("kg").pk, "itens-0-quantidade": "1",
        }
        return {**base, **extra}

    def test_salvar_e_adicionar_outro_volta_ao_formulario_em_branco(self):
        html = self.client.get(reverse("processado-create")).content.decode()
        self.assertIn('name="salvar_e_novo"', html)
        resp = self.client.post(reverse("processado-create"), self.dados(salvar_e_novo="1"))
        self.assertRedirects(resp, reverse("processado-create"), fetch_redirect_response=False)
        self.assertTrue(Processado.objects.filter(nome="Camarão limpo").exists())
        # o fluxo normal continua indo para a lista, e a edição não oferece o botão
        resp = self.client.post(reverse("processado-create"), self.dados(nome="Outro prato"))
        self.assertRedirects(resp, reverse("processado"), fetch_redirect_response=False)
        p = Processado.objects.get(nome="Outro prato")
        self.assertNotIn('name="salvar_e_novo"', self.client.get(reverse("processado-edit", args=[p.pk])).content.decode())

    def test_salvar_e_adicionar_outro_com_erro_nao_grava_e_reexibe(self):
        resp = self.client.post(reverse("processado-create"), self.dados(nome="", salvar_e_novo="1"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Processado.objects.exists())

    def test_tabela_da_ficha_tem_custo_por_linha_e_total_para_a_previa(self):
        html = self.client.get(reverse("processado-create")).content.decode()
        self.assertIn('data-calculo-url="' + reverse("processado-calcular"), html)
        self.assertIn('class="text-nowrap custo-item"', html)
        self.assertGreaterEqual(html.count("total-ficha"), 2)  # topo e rodapé, sempre iguais
        self.assertIn('data-calc-saida="extenso"', html)  # valor por extenso do rendimento
        self.assertIn(reverse("medida-calcular"), html)

    def test_previa_de_custo_usa_a_mesma_conta_e_so_insumos_do_tenant(self):
        alheio = novo_insumo(self.outro, "Alheio", "kg", "1", 9999)
        corpo = {"itens": [
            {"insumo": self.a.pk, "medida": medida("g").pk, "quantidade": "500"},  # 0,5 kg × R$ 39,90 = R$ 19,95
            {"insumo": alheio.pk, "medida": medida("kg").pk, "quantidade": "1"},  # de outro cliente: ignorado
        ]}
        resp = self.client.post(reverse("processado-calcular"), corpo, content_type="application/json")
        dados = resp.json()
        self.assertEqual((dados["itens"][0]["custo"], dados["itens"][1]["custo"], dados["total"]), ("R$ 19,95", "—", "R$ 19,95"))
        # a prévia coincide com o que é gravado
        self.client.post(reverse("processado-create"), self.dados(**{"itens-0-medida": medida("g").pk, "itens-0-quantidade": "500"}))
        self.assertEqual(Processado.objects.get().custo_total, 1995)

    def test_previa_exige_login_e_permissao(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse("processado-calcular"), {}, content_type="application/json").status_code, 302)
        sem = User.objects.create_user(username="s", email="s@t.local", password="SenhaForte123!")
        sem.profile.cliente = self.cliente
        sem.profile.save()
        self.client.force_login(sem)
        self.assertEqual(self.client.post(reverse("processado-calcular"), {}, content_type="application/json").status_code, 403)

    def test_unidades_aparecem_com_nome_completo_e_todas_as_opcoes(self):
        html = self.client.get(reverse("processado-create")).content.decode()
        bloco = html[html.index('name="itens-0-medida"'):]
        bloco = bloco[:bloco.index("</select>")]
        for rotulo in ("Quilo (kg)", "Gramas (g)", "Litro (L)", "Mililitro (ml)", "Unidade (un)", "Colher de sopa (c. sopa)"):
            self.assertIn(f">{rotulo}</option>", bloco)
        saida = html[html.index('name="rendimento_medida"'):]
        saida = saida[:saida.index("</select>")]
        self.assertIn(">Gramas (g)</option>", saida)
        self.assertIn(f'value="{medida("g").pk}"', saida)  # o valor interno continua sendo o id

    def test_extenso_do_rendimento_vem_do_servidor(self):
        resp = self.client.get(reverse("medida-calcular"), {"medida": medida("g").pk, "quantidade": "100"})
        self.assertEqual(resp.json()["quantidade_extenso"], "100 gramas")
        resp = self.client.get(reverse("medida-calcular"), {"medida": medida("kg").pk, "quantidade": "1"})
        self.assertEqual(resp.json()["quantidade_extenso"], "1 quilo")


class ProcessadoUnidadeEExtensoTests(ProcessadoFormularioTests):
    """A unidade da linha começa em "Unidade..." e cada linha mostra a quantidade por extenso."""

    def test_unidade_da_linha_comeca_em_unidade_e_nao_pre_seleciona_a_do_insumo(self):
        html = self.client.get(reverse("processado-create")).content.decode()
        bloco = html[html.index('name="itens-0-medida"'):]
        bloco = bloco[:bloco.index("</select>")]
        self.assertEqual(bloco.count("selected"), 1)
        self.assertIn('<option value="" selected>Unidade...</option>', bloco)
        self.assertNotIn("data-medida", html)
        self.assertNotIn("unidade-do-insumo.js", html)

    def test_extenso_da_quantidade_por_linha_e_custo_quantidade_vezes_preco(self):
        abacaxi = novo_insumo(self.cliente, "Abacaxi", "un", "1", 500)  # R$ 5,00 por unidade
        html = self.client.get(reverse("processado-create")).content.decode()
        self.assertGreaterEqual(html.count("extenso-item"), 2)
        corpo = {"itens": [{"insumo": abacaxi.pk, "medida": medida("un").pk, "quantidade": "2"}]}
        dados = self.client.post(reverse("processado-calcular"), corpo, content_type="application/json").json()
        self.assertEqual((dados["itens"][0]["custo"], dados["itens"][0]["extenso"], dados["total"]), ("R$ 10,00", "2 unidades", "R$ 10,00"))
        self.client.post(reverse("processado-create"), self.dados(**{"nome": "Salada", "itens-0-insumo": abacaxi.pk, "itens-0-medida": medida("un").pk, "itens-0-quantidade": "2"}))
        self.assertEqual(Processado.objects.get(nome="Salada").custo_total, 1000)
