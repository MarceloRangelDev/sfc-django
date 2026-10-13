from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.insumo.tests import medida, novo_insumo
from apps.notificacao.models import DestinatarioNotificacao, Notificacao
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from .models import AlertaEnviado, EstoqueMinimo, HistoricoImutavelError, Lote, MovimentoEstoque, TransferenciaEstoque
from .services import (
    alertas_de_estoque_baixo,
    alertas_de_vencimento,
    definir_minimo,
    editar_movimento,
    gerar_alertas,
    mensagem_saldo_em_estoque,
    registrar_entrada,
    registrar_saida,
    saldo_do_insumo,
    transferir,
    verificar_consistencia,
)

D = Decimal
HOJE = timezone.localdate()  # mesmo fuso do projeto (settings.TIME_ZONE), usado pelos serviços


def cliente(nome, cnpj, matriz=None):
    c = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=nome,
        tipo_estabelecimento=Cliente.TIPO_FILIAL if matriz else Cliente.TIPO_MATRIZ, matriz=matriz,
    )
    for modulo in ("insumo", "estoque"):
        habilitar_modulo(c, modulo)
    return c


def usuario(nome, c, mov=("view", "add", "change"), transf=(), filiais=()):
    grupo = Group.objects.create(name=f"G {nome}")
    perms = list(Permission.objects.filter(content_type__app_label="estoque", codename__in=[f"{a}_movimentoestoque" for a in mov]))
    perms += Permission.objects.filter(content_type__app_label="estoque", codename__in=[f"{a}_transferenciaestoque" for a in transf])
    perms += Permission.objects.filter(content_type__app_label="cliente", codename__in=[f"{a}_itensfilial" for a in filiais])
    grupo.permissions.set(perms)
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = c
    u.profile.save()
    u.groups.add(grupo)
    return u


class ServicosTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("u", self.c)
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)

    def entrada(self, qtd, sigla="kg", codigo="", validade=None, insumo=None, **kw):
        return registrar_entrada(self.user, insumo or self.farinha, D(qtd), medida(sigla), codigo, validade, **kw)

    def saida(self, qtd, sigla="kg", insumo=None, **kw):
        return registrar_saida(self.user, insumo or self.farinha, D(qtd), medida(sigla), **kw)

    def test_entrada_cria_lote_e_saldo_na_unidade_do_insumo(self):
        mov = self.entrada("2", codigo="L1", validade=HOJE + timedelta(days=30))
        self.assertEqual((mov.tipo, mov.quantidade, mov.saldo_lote_apos, mov.responsavel_nome), ("entrada", D("2"), D("2"), "u"))
        self.assertEqual(saldo_do_insumo(self.farinha), D("2"))
        self.entrada("500", sigla="g", codigo="L1")  # 500 g = 0,5 kg no mesmo lote
        self.assertEqual(saldo_do_insumo(self.farinha), D("2.5"))
        self.assertEqual(Lote.objects.filter(insumo=self.farinha).count(), 1)

    def test_entrada_sem_lote_usa_o_lote_vazio(self):
        self.entrada("1")
        self.entrada("1")
        lote = Lote.objects.get()
        self.assertEqual((lote.codigo, lote.validade, lote.saldo), ("", None, D("2")))

    def test_validade_sem_lote_cria_lote_automatico_e_lote_existente_nao_muda_de_validade(self):
        self.entrada("1", validade=HOJE)  # validade vale por si só: lote automático pela validade
        automatico = Lote.objects.get()
        self.assertEqual((automatico.validade, automatico.lote_automatico, automatico.codigo_exibicao), (HOJE, True, ""))
        self.entrada("2", validade=HOJE)  # mesma validade: mesmo lote
        self.assertEqual((Lote.objects.count(), Lote.objects.get().saldo), (1, D("3")))
        self.entrada("1", validade=HOJE + timedelta(days=3))  # outra validade: outro lote
        self.assertEqual(Lote.objects.count(), 2)
        self.entrada("1", codigo="L1", validade=HOJE + timedelta(days=10))
        with self.assertRaises(RegraNegocioError):
            self.entrada("1", codigo="L1", validade=HOJE + timedelta(days=20))
        self.entrada("1", codigo="L1")  # sem validade: aceita, mantém a do lote
        self.assertEqual(Lote.objects.get(codigo="L1").validade, HOJE + timedelta(days=10))

    def test_unidade_de_outra_dimensao_e_quantidade_minuscula_recusadas(self):
        with self.assertRaises(RegraNegocioError):
            self.entrada("1", sigla="L")
        with self.assertRaises(RegraNegocioError):
            self.entrada("0.00001", sigla="g")  # 0,00001 g arredonda para 0 kg
        self.assertFalse(MovimentoEstoque.objects.exists())

    def test_saida_consome_o_lote_que_vence_primeiro(self):
        self.entrada("5", codigo="TARDE", validade=HOJE + timedelta(days=60))
        self.entrada("5", codigo="CEDO", validade=HOJE + timedelta(days=5))
        self.entrada("5")  # sem validade: por último
        movs = self.saida("7")
        self.assertEqual([(m.lote.codigo, m.quantidade) for m in movs], [("CEDO", D("5")), ("TARDE", D("2"))])
        self.assertEqual({l.codigo: l.saldo for l in Lote.objects.all()}, {"CEDO": D("0"), "TARDE": D("3"), "": D("5")})
        self.saida("8")  # esgota TARDE e o sem lote
        self.assertEqual(saldo_do_insumo(self.farinha), D("0"))

    def test_saida_maior_que_o_saldo_nao_grava_nada(self):
        self.entrada("2", codigo="A")
        with self.assertRaises(RegraNegocioError) as ctx:
            self.saida("3")
        self.assertIn("disponível 2", str(ctx.exception))
        self.assertEqual(Lote.objects.get().saldo, D("2"))
        self.assertEqual(MovimentoEstoque.objects.count(), 1)

    def test_saida_em_outra_unidade_de_medida(self):
        self.entrada("2")
        self.saida("500", sigla="g")
        self.assertEqual(saldo_do_insumo(self.farinha), D("1.5"))

    def test_ajustes(self):
        self.entrada("1", tipo="ajuste_entrada")
        self.saida("0.25", tipo="ajuste_saida")
        self.assertEqual(saldo_do_insumo(self.farinha), D("0.75"))
        self.assertEqual(list(MovimentoEstoque.objects.order_by("pk").values_list("tipo", flat=True)), ["ajuste_entrada", "ajuste_saida"])
        with self.assertRaises(RegraNegocioError):
            self.entrada("1", tipo="saida")
        with self.assertRaises(RegraNegocioError):
            self.saida("1", tipo="entrada")

    def test_banco_recusa_saldo_negativo(self):
        self.entrada("1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lote.objects.filter(insumo=self.farinha).update(saldo=D("-1"))

    def test_historico_e_imutavel(self):
        mov = self.entrada("1")
        mov.quantidade = D("99")
        with self.assertRaises(HistoricoImutavelError):
            mov.save()
        with self.assertRaises(HistoricoImutavelError):
            mov.delete()
        with self.assertRaises(HistoricoImutavelError):
            MovimentoEstoque.objects.all().update(observacao="x")
        with self.assertRaises(HistoricoImutavelError):
            MovimentoEstoque.objects.all().delete()
        self.assertEqual(MovimentoEstoque.objects.get().quantidade, D("1"))

    def test_conferencia_detecta_e_corrige(self):
        self.entrada("3", codigo="A")
        self.saida("1")
        self.assertEqual(verificar_consistencia(), [])
        Lote.objects.update(saldo=D("9"))  # corrompe de propósito
        divergencias = verificar_consistencia()
        self.assertEqual([(g, c) for _, g, c in divergencias], [(D("9"), D("2"))])
        verificar_consistencia(corrigir=True)
        self.assertEqual(Lote.objects.get().saldo, D("2"))
        self.assertEqual(verificar_consistencia(), [])

    def test_saldo_em_estoque_bloqueia_exclusao_do_insumo(self):
        self.assertIsNone(mensagem_saldo_em_estoque(self.farinha))
        self.entrada("2")
        self.assertIn("2 kg em estoque", mensagem_saldo_em_estoque(self.farinha))
        self.saida("2")
        self.assertIsNone(mensagem_saldo_em_estoque(self.farinha))

    def test_estoque_isolado_por_unidade(self):
        outra = cliente("Outra", "34028316000103")
        f2 = novo_insumo(outra, "Farinha", "kg", "1", 1000)
        registrar_entrada(self.user, f2, D("9"), medida("kg"))
        self.entrada("1")
        self.assertEqual((saldo_do_insumo(self.farinha), saldo_do_insumo(f2)), (D("1"), D("9")))
        with self.assertRaises(RegraNegocioError):
            self.saida("5")  # o saldo da outra unidade não conta


class TransferenciaTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.user = usuario("u", self.matriz, transf=("view", "add"))
        self.f_matriz = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        self.f_filial = novo_insumo(self.filial, "farinha", "kg", "1", 1000)  # nome igual (sem diferenciar maiúsculas)
        registrar_entrada(self.user, self.f_matriz, D("10"), medida("kg"), "L1", HOJE + timedelta(days=20))

    def test_transferencia_move_saldo_e_leva_lote_e_validade(self):
        t = transferir(self.user, self.matriz, self.filial, [(self.f_matriz, D("4"), medida("kg"))], "reposição")
        self.assertEqual((saldo_do_insumo(self.f_matriz), saldo_do_insumo(self.f_filial)), (D("6"), D("4")))
        lote = Lote.objects.get(insumo=self.f_filial)
        self.assertEqual((lote.codigo, lote.validade), ("L1", HOJE + timedelta(days=20)))
        self.assertEqual((t.origem, t.destino, t.responsavel, t.observacao), (self.matriz, self.filial, self.user, "reposição"))
        tipos = sorted(t.movimentos.values_list("tipo", "cliente_id"))
        self.assertEqual(tipos, sorted([("transferencia_saida", self.matriz.pk), ("transferencia_entrada", self.filial.pk)]))

    def test_transferencia_converte_unidades_e_preserva_varios_lotes(self):
        registrar_entrada(self.user, self.f_matriz, D("2"), medida("kg"), "L2", HOJE + timedelta(days=5))
        transferir(self.user, self.matriz, self.filial, [(self.f_matriz, D("11000"), medida("g"))])  # 11 kg: L2 (2) + L1 (9)
        self.assertEqual({l.codigo: l.saldo for l in Lote.objects.filter(insumo=self.f_filial)}, {"L2": D("2"), "L1": D("9")})

    def test_erros_nao_gravam_nada(self):
        casos = [
            (self.matriz, self.matriz, [(self.f_matriz, D("1"), medida("kg"))]),  # mesma unidade
            (self.matriz, self.filial, []),  # sem itens
            (self.matriz, self.filial, [(self.f_matriz, D("99"), medida("kg"))]),  # saldo insuficiente
            (self.matriz, self.filial, [(self.f_filial, D("1"), medida("kg"))]),  # insumo da outra unidade
        ]
        for origem, destino, itens in casos:
            with self.assertRaises(RegraNegocioError):
                transferir(self.user, origem, destino, itens)
        self.assertFalse(TransferenciaEstoque.objects.exists())
        self.assertEqual(saldo_do_insumo(self.f_matriz), D("10"))
        self.assertFalse(Lote.objects.filter(insumo=self.f_filial).exists())

    def test_destino_sem_o_insumo_ou_inativo_ou_de_outra_matriz(self):
        sem_insumo = novo_insumo(self.matriz, "Açúcar", "kg", "1", 500)
        registrar_entrada(self.user, sem_insumo, D("5"), medida("kg"))
        with self.assertRaises(RegraNegocioError) as ctx:
            transferir(self.user, self.matriz, self.filial, [(sem_insumo, D("1"), medida("kg"))])
        self.assertIn("Açúcar", str(ctx.exception))
        self.assertEqual(saldo_do_insumo(sem_insumo), D("5"))  # a saída da origem foi desfeita
        outra = cliente("Outra", "34028316000103")
        with self.assertRaises(RegraNegocioError):
            transferir(self.user, self.matriz, outra, [(self.f_matriz, D("1"), medida("kg"))])
        self.filial.ativo = False
        self.filial.save()
        with self.assertRaises(RegraNegocioError):
            transferir(self.user, self.matriz, self.filial, [(self.f_matriz, D("1"), medida("kg"))])

    def test_transferencia_e_imutavel(self):
        t = transferir(self.user, self.matriz, self.filial, [(self.f_matriz, D("1"), medida("kg"))])
        with self.assertRaises(HistoricoImutavelError):
            t.delete()
        t.observacao = "x"
        with self.assertRaises(HistoricoImutavelError):
            t.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransferenciaEstoque.objects.create(origem=self.matriz, destino=self.matriz)


class AlertasTests(TestCase):
    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.user = usuario("gestor", self.c)
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)

    def test_estoque_baixo_exige_minimo_definido(self):
        self.assertEqual(list(alertas_de_estoque_baixo([self.c])), [])  # sem mínimo: sem alerta
        definir_minimo(None, self.farinha, D("5"))
        self.assertEqual([i.nome for i in alertas_de_estoque_baixo([self.c])], ["Farinha"])  # saldo 0 < 5
        registrar_entrada(self.user, self.farinha, D("5"), medida("kg"))
        self.assertEqual(list(alertas_de_estoque_baixo([self.c])), [])  # saldo = mínimo: ok
        definir_minimo(None, self.farinha, D("0"))
        registrar_saida(self.user, self.farinha, D("5"), medida("kg"))
        self.assertEqual(list(alertas_de_estoque_baixo([self.c])), [])  # mínimo 0 = sem alerta

    def test_minimo_negativo_recusado_e_auditado(self):
        with self.assertRaises(RegraNegocioError):
            definir_minimo(None, self.farinha, D("-1"))
        definir_minimo(None, self.farinha, D("3"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="estoque", acao="editar").exists())
        self.assertEqual(EstoqueMinimo.objects.get().minimo, D("3"))

    def test_vencimento(self):
        registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "VENCIDO", HOJE - timedelta(days=1))
        registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "LOGO", HOJE + timedelta(days=7))
        registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "LONGE", HOJE + timedelta(days=8))
        registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "SEMVALIDADE")
        self.assertEqual([l.codigo for l in alertas_de_vencimento([self.c])], ["VENCIDO", "LOGO"])
        registrar_saida(self.user, self.farinha, D("2"), medida("kg"))  # esgota os dois primeiros
        self.assertEqual(list(alertas_de_vencimento([self.c])), [])  # lote sem saldo não alerta

    def test_gerar_alertas_notifica_a_unidade_uma_vez_por_dia(self):
        definir_minimo(None, self.farinha, D("5"))
        registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "LOGO", HOJE + timedelta(days=2))
        sem_permissao = User.objects.create_user(username="semperm", email="semperm@t.local", password="SenhaForte123!")
        sem_permissao.profile.cliente = self.c
        sem_permissao.profile.save()
        outra = cliente("Outra", "34028316000103")
        usuario("outro", outra)

        self.assertEqual(gerar_alertas([self.c]), {"baixo": 1, "vencimento": 1})
        notificacoes = list(Notificacao.objects.order_by("pk"))
        self.assertEqual([n.titulo for n in notificacoes], ["Estoque baixo", "Itens vencendo"])
        self.assertIn("Farinha", notificacoes[0].mensagem)
        self.assertEqual(set(DestinatarioNotificacao.objects.values_list("usuario__username", flat=True)), {"gestor"})  # só quem vê o estoque, só da unidade
        self.assertEqual(gerar_alertas([self.c]), {"baixo": 0, "vencimento": 0})  # idempotente no mesmo dia
        self.assertEqual(Notificacao.objects.count(), 2)
        AlertaEnviado.objects.update(data=HOJE - timedelta(days=1))  # no dia seguinte avisa de novo
        self.assertEqual(gerar_alertas([self.c]), {"baixo": 1, "vencimento": 1})

    def test_comando_gera_alertas(self):
        definir_minimo(None, self.farinha, D("5"))
        call_command("gerar_alertas_estoque", verbosity=0)
        call_command("gerar_alertas_estoque", verbosity=0)
        self.assertEqual(Notificacao.objects.filter(titulo="Estoque baixo").count(), 1)

    def test_saida_que_cruza_o_minimo_notifica_na_hora(self):
        definir_minimo(None, self.farinha, D("5"))
        registrar_entrada(self.user, self.farinha, D("10"), medida("kg"))
        with self.captureOnCommitCallbacks(execute=True):
            registrar_saida(self.user, self.farinha, D("2"), medida("kg"))  # 8: ainda acima
        self.assertFalse(Notificacao.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            registrar_saida(self.user, self.farinha, D("4"), medida("kg"))  # 4 < 5
        self.assertEqual(Notificacao.objects.get().titulo, "Estoque baixo")
        with self.captureOnCommitCallbacks(execute=True):
            registrar_saida(self.user, self.farinha, D("1"), medida("kg"))  # já avisado hoje
        self.assertEqual(Notificacao.objects.count(), 1)


class TelasTests(TestCase):
    def setUp(self):
        self.matriz = cliente("Matriz", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.matriz)
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.matriz, mov=("view", "add", "change"), transf=("view", "add"))
        self.client.force_login(self.user)
        self.farinha = novo_insumo(self.matriz, "Farinha", "kg", "1", 1000)
        self.ovo = novo_insumo(self.matriz, "Ovo", "un", "12", 1200)
        self.f_filial = novo_insumo(self.filial, "Farinha", "kg", "1", 1000)
        registrar_entrada(self.user, self.farinha, D("10"), medida("kg"), "L1", HOJE + timedelta(days=3))

    def nomes(self, nome="estoque", **p):
        return [i.nome for i in self.client.get(reverse(nome), p).context["insumos"]]

    def test_saldos_alimentados_pelos_insumos_cadastrados(self):
        resp = self.client.get(reverse("estoque"))
        por_nome = {i.nome: i for i in resp.context["insumos"]}
        self.assertEqual(set(por_nome), {"Farinha", "Ovo"})  # Ovo sem movimento aparece com saldo 0
        self.assertEqual((por_nome["Farinha"].saldo, por_nome["Ovo"].saldo), (D("10"), D("0")))
        self.assertEqual(por_nome["Ovo"].situacao, "Zerado")

    def test_filtros_de_situacao_e_ordenacao(self):
        definir_minimo(None, self.farinha, D("20"))
        self.assertEqual(self.nomes(situacao="baixo"), ["Farinha"])
        self.assertEqual(self.nomes(situacao="zerado"), ["Ovo"])
        self.assertEqual(self.nomes(situacao="vencendo"), ["Farinha"])  # vence em 3 dias (limite padrão: 7)
        self.assertEqual(self.nomes(q="ov"), ["Ovo"])
        self.assertEqual(self.nomes(sort="saldo", dir="desc"), ["Farinha", "Ovo"])
        self.assertEqual(self.nomes(sort="injeção"), ["Farinha", "Ovo"])

    def test_matriz_ve_saldos_separados_por_unidade_com_permissao(self):
        registrar_entrada(self.user, self.f_filial, D("3"), medida("kg"))
        self.assertEqual(self.nomes(), ["Farinha", "Ovo"])  # sem permissão de filiais: só a matriz
        self.client.force_login(usuario("m", self.matriz, filiais=("view",)))
        resp = self.client.get(reverse("estoque"))
        saldos = sorted((i.cliente.nome_razao_social, i.nome, i.saldo) for i in resp.context["insumos"])
        self.assertEqual(saldos, [("Filial", "Farinha", D("3")), ("Matriz", "Farinha", D("10")), ("Matriz", "Ovo", D("0"))])
        self.assertTrue(resp.context["mostrar_unidade"])
        self.assertEqual(self.nomes(unidade=self.filial.pk), ["Farinha"])
        self.client.force_login(usuario("f", self.filial))
        self.assertEqual(self.nomes(), ["Farinha"])  # a filial nunca vê a matriz

    def test_exportacao_respeita_filtro_e_escopo(self):
        resp = self.client.get(reverse("estoque"), {"export": "xlsx", "q": "farinha"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([(l[0], l[1], l[3]) for l in linhas[1:]], [("Farinha", "Matriz", "10")])
        self.assertTrue(self.client.get(reverse("estoque"), {"export": "pdf"}).content.startswith(b"%PDF"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="estoque", acao="exportar").exists())

    def test_registrar_movimentos_pela_tela(self):
        dados = {"insumo": self.ovo.pk, "tipo": "entrada", "quantidade": "24", "medida": medida("un").pk, "lote": "OVO1", "validade": str(HOJE + timedelta(days=15)), "observacao": "compra"}
        resp = self.client.post(reverse("estoque-movimentar"), dados)
        self.assertRedirects(resp, reverse("estoque"), fetch_redirect_response=False)
        self.assertEqual(saldo_do_insumo(self.ovo), D("24"))
        resp = self.client.post(reverse("estoque-movimentar"), {**dados, "tipo": "saida", "quantidade": "4", "lote": "", "validade": "", "salvar_e_novo": "1"})
        self.assertRedirects(resp, reverse("estoque-movimentar"), fetch_redirect_response=False)
        self.assertEqual(saldo_do_insumo(self.ovo), D("20"))
        self.assertContains(self.client.get(reverse("estoque-insumo", args=[self.ovo.pk])), "OVO1")

    def test_validacoes_da_tela(self):
        base = {"insumo": self.ovo.pk, "tipo": "entrada", "quantidade": "1", "medida": medida("un").pk}
        alheio = novo_insumo(self.outra, "Alheio", "kg", "1", 100)
        casos = [
            {**base, "quantidade": "0"}, {**base, "quantidade": "-2"}, {**base, "quantidade": "abc"},
            {**base, "insumo": alheio.pk}, {**base, "insumo": self.f_filial.pk},  # outra unidade
            {**base, "tipo": "saida", "quantidade": "5"},  # sem saldo
            {**base, "tipo": "saida", "quantidade": "1", "lote": "X"},  # saída não escolhe lote
            {**base, "medida": medida("kg").pk},  # dimensão incompatível
            {**base, "tipo": "transferencia_entrada"},  # tipo que não é manual
        ]
        for dados in casos:
            self.assertEqual(self.client.post(reverse("estoque-movimentar"), dados).status_code, 200, dados)
        self.assertEqual(MovimentoEstoque.objects.count(), 1)  # só a entrada do setUp

    def test_permissoes(self):
        self.client.force_login(usuario("v", self.matriz, mov=("view",)))
        self.assertEqual(self.client.get(reverse("estoque")).status_code, 200)
        self.assertEqual(self.client.get(reverse("estoque-movimentar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("estoque-minimo", args=[self.farinha.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("estoque-transferencia-nova")).status_code, 403)
        self.assertEqual(self.client.get(reverse("estoque-transferencias")).status_code, 403)
        self.client.force_login(User.objects.create_user(username="sem", email="sem@t.local", password="SenhaForte123!"))
        for nome in ("estoque", "estoque-movimentos", "estoque-alertas"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403)

    def test_minimo_pela_tela(self):
        url = reverse("estoque-minimo", args=[self.farinha.pk])
        self.assertEqual(self.client.post(url, {"minimo": "-1"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"minimo": "7,5"}).status_code, 302)
        self.assertEqual(EstoqueMinimo.objects.get(insumo=self.farinha).minimo, D("7.5"))
        # fora do escopo: insumo de outra unidade / filial sem permissão → 404
        self.assertEqual(self.client.get(reverse("estoque-minimo", args=[self.f_filial.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("estoque-insumo", args=[self.f_filial.pk])).status_code, 404)

    def test_movimentos_filtros_escopo_e_exportacao(self):
        registrar_saida(self.user, self.farinha, D("1"), medida("kg"), "consumo")
        registrar_entrada(usuario("outro", self.outra), novo_insumo(self.outra, "Alheio", "kg", "1", 1), D("1"), medida("kg"))
        movs = lambda **p: [(m.insumo.nome, m.tipo) for m in self.client.get(reverse("estoque-movimentos"), p).context["movimentos"]]
        self.assertEqual(movs(), [("Farinha", "saida"), ("Farinha", "entrada")])  # mais recentes primeiro; sem a outra empresa
        self.assertEqual(movs(tipo="entrada"), [("Farinha", "entrada")])
        self.assertEqual(movs(q="L1"), [("Farinha", "saida"), ("Farinha", "entrada")])  # o lote L1 aparece nas duas
        self.assertEqual(movs(q="inexistente"), [])
        self.assertEqual(movs(data_de=str(HOJE + timedelta(days=1))), [])
        self.assertEqual(movs(data_ate=str(HOJE)), [("Farinha", "saida"), ("Farinha", "entrada")])
        resp = self.client.get(reverse("estoque-movimentos"), {"export": "xlsx", "tipo": "saida"})
        linhas = list(openpyxl.load_workbook(BytesIO(resp.content)).active.values)
        self.assertEqual([(l[2], l[3], l[4]) for l in linhas[1:]], [("Farinha", "Saída", "-1 kg")])

    def test_transferencia_pela_tela(self):
        url = reverse("estoque-transferencia-nova")
        form = self.client.get(url).context["form"]
        self.assertEqual(list(form.fields["destino"].queryset), [self.filial])  # só a outra unidade da estrutura
        dados = {
            "destino": self.filial.pk, "observacao": "reposição", "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.farinha.pk, "itens-0-quantidade": "4", "itens-0-medida": medida("kg").pk,
        }
        resp = self.client.post(url, dados)
        t = TransferenciaEstoque.objects.get()
        self.assertRedirects(resp, reverse("estoque-transferencia", args=[t.pk]), fetch_redirect_response=False)
        self.assertEqual((saldo_do_insumo(self.farinha), saldo_do_insumo(self.f_filial)), (D("6"), D("4")))
        self.assertContains(self.client.get(reverse("estoque-transferencia", args=[t.pk])), "L1")
        self.assertContains(self.client.get(reverse("estoque-transferencias")), "reposição")

    def test_transferencia_validacoes_da_tela(self):
        url = reverse("estoque-transferencia-nova")
        base = {
            "destino": self.filial.pk, "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.farinha.pk, "itens-0-quantidade": "1", "itens-0-medida": medida("kg").pk,
        }
        casos = [
            {**base, "destino": self.matriz.pk},  # para a própria unidade
            {**base, "destino": self.outra.pk},  # outra matriz
            {**base, "itens-0-quantidade": "99"},  # saldo insuficiente
            {**base, "itens-0-insumo": self.f_filial.pk},  # insumo que não é da origem
            {**base, "itens-0-insumo": self.ovo.pk},  # destino não tem "Ovo"
            {**base, "itens-TOTAL_FORMS": "0"},  # sem itens
        ]
        for dados in casos:
            self.assertEqual(self.client.post(url, dados).status_code, 200, dados)
        self.assertFalse(TransferenciaEstoque.objects.exists())
        self.assertEqual(saldo_do_insumo(self.farinha), D("10"))

    def test_transferencia_so_com_permissao_e_escopo_de_leitura(self):
        t = transferir(self.user, self.matriz, self.filial, [(self.farinha, D("1"), medida("kg"))])
        sem = usuario("sem_transf", self.matriz, mov=("view", "add"))  # sem as permissões de transferência
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("estoque-transferencia-nova")).status_code, 403)
        self.assertEqual(self.client.get(reverse("estoque-transferencia", args=[t.pk])).status_code, 403)
        # a filial (destino) enxerga a transferência recebida; outra empresa não
        self.client.force_login(usuario("f", self.filial, transf=("view",)))
        self.assertEqual(self.client.get(reverse("estoque-transferencia", args=[t.pk])).status_code, 200)
        self.assertEqual([x.pk for x in self.client.get(reverse("estoque-transferencias")).context["transferencias"]], [t.pk])
        self.client.force_login(usuario("o", self.outra, transf=("view",)))
        self.assertEqual(self.client.get(reverse("estoque-transferencia", args=[t.pk])).status_code, 404)
        self.assertEqual(list(self.client.get(reverse("estoque-transferencias")).context["transferencias"]), [])

    def test_filial_transfere_para_a_matriz_mas_nao_retira_dela(self):
        registrar_entrada(self.user, self.f_filial, D("5"), medida("kg"))
        filial_user = usuario("f", self.filial, transf=("view", "add"))
        self.client.force_login(filial_user)
        form = self.client.get(reverse("estoque-transferencia-nova")).context["form"]
        self.assertEqual(list(form.fields["destino"].queryset), [self.matriz])
        dados = {
            "destino": self.matriz.pk, "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-insumo": self.f_filial.pk, "itens-0-quantidade": "2", "itens-0-medida": medida("kg").pk,
        }
        self.assertEqual(self.client.post(reverse("estoque-transferencia-nova"), dados).status_code, 302)
        self.assertEqual((saldo_do_insumo(self.f_filial), saldo_do_insumo(self.farinha)), (D("3"), D("12")))
        # tentar retirar insumo da matriz (origem forçada = filial): recusado
        dados["itens-0-insumo"] = self.farinha.pk
        self.assertEqual(self.client.post(reverse("estoque-transferencia-nova"), dados).status_code, 200)
        self.assertEqual(saldo_do_insumo(self.farinha), D("12"))

    def test_pagina_de_alertas(self):
        definir_minimo(None, self.farinha, D("50"))
        ctx = self.client.get(reverse("estoque-alertas")).context
        self.assertEqual([i.nome for i in ctx["baixos"]], ["Farinha"])
        self.assertEqual([(l.codigo, l.dias, l.vencido) for l in ctx["lotes"]], [("L1", 3, False)])
        self.assertContains(self.client.get(reverse("estoque")), "abaixo do mínimo")

    def test_insumo_com_estoque_nao_troca_de_unidade_nem_e_excluido(self):
        grupo = Group.objects.create(name="insumo-total")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="insumo"))
        self.user.groups.add(grupo)
        dados = {"nome": "Farinha", "medida": medida("g").pk, "quantidade": "1000", "preco_pago": "10,00", "ativo": "on"}
        resp = self.client.post(reverse("insumo-edit", args=[self.farinha.pk]), dados)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "movimentações de estoque")
        self.farinha.refresh_from_db()
        self.assertEqual(self.farinha.medida.sigla, medida("kg").sigla)
        # exclusão bloqueada com saldo; liberada com saldo zerado
        self.client.post(reverse("insumo-excluir", args=[self.farinha.pk]))
        self.farinha.refresh_from_db()
        self.assertTrue(self.farinha.ativo)
        registrar_saida(self.user, self.farinha, D("10"), medida("kg"))
        self.client.post(reverse("insumo-excluir", args=[self.farinha.pk]))
        self.farinha.refresh_from_db()
        self.assertFalse(self.farinha.ativo)


class PermissoesPadraoTests(TestCase):
    def test_transferencia_so_para_administrador_e_gestor(self):
        call_command("seed_acl", verbosity=0)
        tem = lambda grupo, cod: Group.objects.get(name=grupo).permissions.filter(codename=cod).exists()
        for cod in ("view_transferenciaestoque", "add_transferenciaestoque"):
            self.assertTrue(tem("Administrador", cod) and tem("Gestor", cod), cod)
            self.assertFalse(tem("Operador", cod) or tem("Visualizador", cod), cod)
        self.assertTrue(tem("Administrador", "delete_transferenciaestoque"))
        self.assertTrue(tem("Operador", "add_movimentoestoque"))  # operador movimenta, mas não transfere
        self.assertFalse(tem("Visualizador", "add_movimentoestoque"))


class EdicaoDeMovimentoTests(TestCase):
    """Edição de movimentação: saldos do lote e do histórico recalculados, sem saldo negativo,
    com auditoria, restrita ao tenant e às movimentações que não são de transferência."""

    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.outra = cliente("Outra", "34028316000103")
        self.user = usuario("u", self.c)
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)
        self.entrada = registrar_entrada(self.user, self.farinha, D("10"), medida("kg"), "L1", HOJE + timedelta(days=30))
        self.saida = registrar_saida(self.user, self.farinha, D("4"), medida("kg"))[0]
        self.escopo = Cliente.objects.filter(pk=self.c.pk)

    def editar(self, mov, qtd, sigla="kg", **kw):
        return editar_movimento(None, mov.pk, kw.pop("escopo", self.escopo), D(qtd), medida(sigla), kw.pop("data", timezone.now()), **kw)

    def saldo_e_historico_consistentes(self):
        self.assertEqual(verificar_consistencia(), [])

    def test_aumentar_entrada_recalcula_lote_e_historico_posterior(self):
        self.editar(self.entrada, "12")
        self.assertEqual(saldo_do_insumo(self.farinha), D("8"))
        self.entrada.refresh_from_db()
        self.saida.refresh_from_db()
        self.assertEqual((self.entrada.quantidade, self.entrada.saldo_lote_apos, self.saida.saldo_lote_apos), (D("12"), D("12"), D("8")))
        self.assertIsNotNone(self.entrada.editado_em)
        self.saldo_e_historico_consistentes()

    def test_reducao_que_deixaria_saldo_negativo_e_recusada_sem_alterar_nada(self):
        with self.assertRaises(RegraNegocioError):
            self.editar(self.entrada, "3")  # 3 - 4 < 0 no histórico
        self.entrada.refresh_from_db()
        self.assertEqual((self.entrada.quantidade, saldo_do_insumo(self.farinha)), (D("10"), D("6")))
        self.saldo_e_historico_consistentes()

    def test_editar_saida_para_mais_e_para_menos(self):
        self.editar(self.saida, "9")
        self.assertEqual(saldo_do_insumo(self.farinha), D("1"))
        with self.assertRaises(RegraNegocioError):
            self.editar(self.saida, "11")  # só há 10 no lote
        self.editar(self.saida, "1")
        self.assertEqual(saldo_do_insumo(self.farinha), D("9"))
        self.saldo_e_historico_consistentes()

    def test_unidade_diferente_e_convertida(self):
        self.editar(self.saida, "500", sigla="g")  # 0,5 kg
        self.saida.refresh_from_db()
        self.assertEqual((self.saida.quantidade, saldo_do_insumo(self.farinha)), (D("0.5"), D("9.5")))

    def test_edicao_e_auditada_com_antes_e_depois(self):
        self.editar(self.entrada, "12", observacao="conferido")
        registro = RegistroAuditoria.objects.filter(acao="editar", modulo="estoque").latest("pk")
        campos = {a["campo"]: (a["antes"], a["depois"]) for a in registro.alteracoes}
        self.assertEqual(campos["quantidade"], ("10", "12"))
        self.assertEqual(campos["observacao"], (None, "conferido"))
        self.assertNotIn("lote", campos)

    def test_movimento_de_transferencia_nao_e_editavel(self):
        filial = cliente("Filial", "11444777000161", self.c)
        novo_insumo(filial, "Farinha", "kg", "1", 1000)
        transferir(self.user, self.c, filial, [(self.farinha, D("1"), medida("kg"))])
        mov = MovimentoEstoque.objects.filter(transferencia__isnull=False).first()
        with self.assertRaises(RegraNegocioError):
            self.editar(mov, "2")

    def test_movimento_de_outro_tenant_e_invisivel(self):
        with self.assertRaises(RegraNegocioError):
            self.editar(self.entrada, "12", escopo=Cliente.objects.filter(pk=self.outra.pk))
        self.entrada.refresh_from_db()
        self.assertEqual(self.entrada.quantidade, D("10"))

    def test_data_futura_recusada(self):
        with self.assertRaises(RegraNegocioError):
            self.editar(self.entrada, "10", data=timezone.now() + timedelta(days=10))

    def test_validade_so_muda_em_lote_com_um_unico_movimento(self):
        with self.assertRaises(RegraNegocioError):
            self.editar(self.entrada, "10", validade=HOJE + timedelta(days=60))  # L1 tem a saída
        sozinho = registrar_entrada(self.user, self.farinha, D("1"), medida("kg"), "", HOJE + timedelta(days=5))
        nova = HOJE + timedelta(days=9)
        self.editar(sozinho, "1", validade=nova)
        lote = Lote.objects.get(pk=sozinho.lote_id)
        self.assertEqual((lote.validade, lote.codigo), (nova, f"VAL-{nova:%Y%m%d}"))

    def test_tela_edita_e_exige_permissao_e_tenant(self):
        self.client.force_login(self.user)
        url = reverse("estoque-movimento-editar", args=[self.entrada.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        corpo = {
            "quantidade": "12", "medida": medida("kg").pk, "data": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "validade": "", "observacao": "ok",
        }
        resposta = self.client.post(url, {**corpo, "validade": str(HOJE + timedelta(days=30))})
        self.assertRedirects(resposta, reverse("estoque-movimentos"), fetch_redirect_response=False)
        self.assertEqual(saldo_do_insumo(self.farinha), D("8"))
        # recusa por saldo negativo volta ao formulário com a mensagem
        ruim = self.client.post(url, {**corpo, "quantidade": "1", "validade": str(HOJE + timedelta(days=30))})
        self.assertEqual(ruim.status_code, 200)
        self.assertContains(ruim, "negativo")
        # somente leitura: 403; outra empresa: 404
        self.client.force_login(usuario("v", self.c, mov=("view",)))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(usuario("x", self.outra))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, corpo).status_code, 404)

    def test_lista_mostra_botao_editar_so_para_quem_pode(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("estoque-movimentos")), reverse("estoque-movimento-editar", args=[self.entrada.pk]))
        self.client.force_login(usuario("v", self.c, mov=("view",)))
        self.assertNotContains(self.client.get(reverse("estoque-movimentos")), "/editar")

    def test_historico_continua_imutavel_fora_do_servico(self):
        self.entrada.quantidade = D("99")
        with self.assertRaises(HistoricoImutavelError):
            self.entrada.save()
        with self.assertRaises(HistoricoImutavelError):
            MovimentoEstoque.objects.filter(pk=self.entrada.pk).update(quantidade=1)


class MinimoNaListaDeSaldosTests(TestCase):
    """O atalho "Definir mínimo" volta a aparecer na lista de saldos para quem pode alterar o estoque."""

    def setUp(self):
        self.c = cliente("Empresa", "11222333000181")
        self.filial = cliente("Filial", "11444777000161", self.c)
        self.outra = cliente("Outra", "34028316000103")
        self.farinha = novo_insumo(self.c, "Farinha", "kg", "1", 1000)
        self.da_filial = novo_insumo(self.filial, "Açúcar", "kg", "1", 500)
        self.link = lambda insumo: reverse("estoque-minimo", args=[insumo.pk])

    def test_mostra_o_atalho_e_o_valor_do_minimo(self):
        definir_minimo(None, self.farinha, D("5"))
        self.client.force_login(usuario("u", self.c))
        html = self.client.get(reverse("estoque")).content.decode()
        self.assertIn(self.link(self.farinha), html)
        self.assertIn("5 kg", html)  # a coluna Mínimo continua mostrando a quantidade

    def test_sem_permissao_de_alterar_nao_mostra_o_atalho(self):
        self.client.force_login(usuario("v", self.c, mov=("view",)))
        self.assertNotIn(self.link(self.farinha), self.client.get(reverse("estoque")).content.decode())

    def test_matriz_so_define_minimo_da_filial_com_permissao_de_itens_da_filial(self):
        self.client.force_login(usuario("m", self.c, filiais=("view",)))  # vê a filial, mas não altera
        html = self.client.get(reverse("estoque")).content.decode()
        self.assertIn("Açúcar", html)
        self.assertNotIn(self.link(self.da_filial), html)
        self.assertEqual(self.client.get(self.link(self.da_filial)).status_code, 404)  # o servidor também recusa
        self.client.force_login(usuario("m2", self.c, filiais=("view", "change")))
        self.assertIn(self.link(self.da_filial), self.client.get(reverse("estoque")).content.decode())
        self.assertEqual(self.client.get(self.link(self.da_filial)).status_code, 200)

    def test_nunca_mostra_atalho_de_outra_empresa(self):
        alheio = novo_insumo(self.outra, "Alheio", "kg", "1", 100)
        self.client.force_login(usuario("u", self.c))
        html = self.client.get(reverse("estoque")).content.decode()
        self.assertNotIn(self.link(alheio), html)
        self.assertEqual(self.client.get(self.link(alheio)).status_code, 404)

    def test_tela_do_minimo_mostra_o_campo_e_grava(self):
        self.client.force_login(usuario("u", self.c))
        resp = self.client.get(self.link(self.farinha))
        self.assertContains(resp, 'name="minimo"')
        self.client.post(self.link(self.farinha), {"minimo": "7,5"})
        self.assertEqual(EstoqueMinimo.objects.get(insumo=self.farinha).minimo, D("7.5"))
