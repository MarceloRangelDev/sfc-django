from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.modulo.models import Segmento

from . import canais
from .models import DestinatarioNotificacao, Notificacao
from .services import enviar_notificacao

SENHA = "SenhaForte123!"


class CanalOk(canais.CanalEntrega):
    nome = "ok"

    def enviar(self, usuario, titulo, mensagem, link):
        return None


class CanalQuebrado(canais.CanalEntrega):
    nome = "quebrado"

    def enviar(self, usuario, titulo, mensagem, link):
        raise canais.ErroDeEntrega("dispositivo expirado")


def usuario(username, cliente, perfil):
    novo = User.objects.create_user(username=username, email=f"{username}@teste.local", password=SENHA)
    novo.profile.cliente = cliente
    novo.profile.save()
    novo.groups.add(Group.objects.get(name=perfil))
    return novo


class Base(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.cliente_a = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A",
            segmento=Segmento.objects.get(slug="produto"),
        )
        self.filial_a = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Filial A",
            tipo_estabelecimento="filial", matriz=self.cliente_a, segmento=Segmento.objects.get(slug="produto"),
        )
        self.cliente_b = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="04252011000110", nome_razao_social="Empresa B",
            segmento=Segmento.objects.get(slug="servico"),
        )
        self.admin = usuario("adm", self.cliente_a, "Administrador")
        self.gestor_a = usuario("gestor_a", self.cliente_a, "Gestor")
        self.op_filial = usuario("op_filial", self.filial_a, "Operador")
        self.op_b = usuario("op_b", self.cliente_b, "Operador")
        patcher = mock.patch.object(canais, "CANAIS", [CanalOk()])
        patcher.start()
        self.addCleanup(patcher.stop)

    def dados(self, **extra):
        base = {"titulo": "Aviso", "mensagem": "Texto do aviso", "link": "", "tipo_destinatario": "todos", "confirmado": "1"}
        base.update(extra)
        return base

    def enviar(self, quem, **extra):
        self.client.login(username=quem, password=SENHA)
        return self.client.post(reverse("notificacao-create"), self.dados(**extra))

    def destinatarios(self):
        return set(DestinatarioNotificacao.objects.values_list("usuario__username", flat=True))


class EnvioTests(Base):
    def test_admin_envia_para_todos(self):
        self.assertRedirects(self.enviar("adm"), reverse("notificacao-list"))
        self.assertEqual(self.destinatarios(), {"adm", "gestor_a", "op_filial", "op_b"})

    def test_gestor_envia_para_todos_mas_so_da_propria_estrutura(self):
        self.enviar("gestor_a")
        self.assertEqual(self.destinatarios(), {"adm", "gestor_a", "op_filial"})

    def test_envio_por_segmento(self):
        self.enviar("adm", tipo_destinatario="segmento", segmentos=[Segmento.objects.get(slug="servico").pk])
        self.assertEqual(self.destinatarios(), {"op_b"})

    def test_gestor_nao_alcanca_segmento_de_outra_estrutura(self):
        self.enviar("gestor_a", tipo_destinatario="segmento", segmentos=[Segmento.objects.get(slug="servico").pk])
        self.assertEqual(Notificacao.objects.count(), 0)

    def test_envio_para_usuarios_especificos(self):
        self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_filial.pk, self.op_b.pk])
        self.assertEqual(self.destinatarios(), {"op_filial", "op_b"})

    def test_gestor_nao_envia_para_usuario_de_outra_estrutura(self):
        self.enviar("gestor_a", tipo_destinatario="usuarios", usuarios=[self.op_b.pk])
        self.assertEqual(Notificacao.objects.count(), 0)

    def test_usuario_inativo_nao_recebe(self):
        User.objects.filter(pk=self.op_filial.pk).update(is_active=False)
        self.enviar("adm")
        self.assertNotIn("op_filial", self.destinatarios())

    def test_confirmacao_mostra_quantidade_e_nao_envia(self):
        self.client.login(username="adm", password=SENHA)
        dados = self.dados()
        del dados["confirmado"]
        resposta = self.client.post(reverse("notificacao-create"), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Confirme o envio")
        self.assertEqual(resposta.context["confirmacao"]["total"], 4)
        self.assertEqual(Notificacao.objects.count(), 0)

    def test_link_externo_e_rejeitado(self):
        for link in ("http://malicioso.com", "//malicioso.com", "javascript:alert(1)"):
            self.enviar("adm", link=link)
        self.assertEqual(Notificacao.objects.count(), 0)
        self.enviar("adm", link="/insumo")
        self.assertEqual(Notificacao.objects.get().link, "/insumo")

    def test_usuario_sem_permissao_nao_envia(self):
        self.client.login(username="op_filial", password=SENHA)
        self.assertEqual(self.client.post(reverse("notificacao-create"), self.dados()).status_code, 403)
        self.assertEqual(Notificacao.objects.count(), 0)

    def test_envio_e_auditado(self):
        self.enviar("adm")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="criar", modulo="notificacao").exists())


class EntregaTests(Base):
    def test_entrega_bem_sucedida_registra_status_e_data(self):
        self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk])
        item = DestinatarioNotificacao.objects.get()
        self.assertEqual(item.status_entrega, "entregue")
        self.assertIsNotNone(item.entregue_em)
        self.assertEqual(item.erro_entrega, "")

    def test_falha_de_entrega_registra_o_erro(self):
        with mock.patch.object(canais, "CANAIS", [CanalQuebrado()]):
            self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk])
        item = DestinatarioNotificacao.objects.get()
        self.assertEqual((item.status_entrega, item.erro_entrega), ("falha", "dispositivo expirado"))
        self.assertIsNone(item.entregue_em)

    def test_um_canal_que_entrega_basta(self):
        with mock.patch.object(canais, "CANAIS", [CanalQuebrado(), CanalOk()]):
            self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk])
        self.assertEqual(DestinatarioNotificacao.objects.get().status_entrega, "entregue")

    def test_webpush_sem_dispositivo_inscrito_vira_falha(self):
        with mock.patch.object(canais, "CANAIS", [canais.CanalWebPush()]):
            self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk])
        item = DestinatarioNotificacao.objects.get()
        self.assertEqual(item.status_entrega, "falha")
        self.assertIn("sem dispositivo", item.erro_entrega)

    def test_webpush_envia_para_o_dispositivo_inscrito(self):
        from webpush.models import PushInformation, SubscriptionInfo

        inscricao = SubscriptionInfo.objects.create(browser="x", endpoint="https://push.exemplo/1", auth="a", p256dh="b")
        PushInformation.objects.create(user=self.op_b, subscription=inscricao)
        with mock.patch.object(canais, "CANAIS", [canais.CanalWebPush()]), \
                mock.patch.object(canais, "send_notification_to_user") as envio:
            self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk], titulo="Oi", link="/insumo")
        self.assertEqual(DestinatarioNotificacao.objects.get().status_entrega, "entregue")
        usuario_enviado, payload = envio.call_args.args
        self.assertEqual(usuario_enviado, self.op_b)
        self.assertIn('"head": "Oi"', payload)

    def test_inscricao_push_exige_login_e_so_grava_para_o_proprio_usuario(self):
        corpo = '{"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"auth": "a", "p256dh": "b"}}}'
        self.assertEqual(self.client.post(reverse("notificacao-inscricao-push"), corpo, content_type="application/json").status_code, 302)
        self.client.login(username="op_b", password=SENHA)
        resposta = self.client.post(reverse("notificacao-inscricao-push"), corpo, content_type="application/json")
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(self.op_b.webpush_info.count(), 1)
        self.assertEqual(self.gestor_a.webpush_info.count(), 0)
        invalida = self.client.post(reverse("notificacao-inscricao-push"), "{}", content_type="application/json")
        self.assertEqual(invalida.status_code, 400)

    def test_inscricao_push_rejeita_endpoint_fora_dos_provedores_conhecidos(self):
        self.client.login(username="op_b", password=SENHA)
        for endpoint in ("http://localhost:8000/interno", "https://servidor-interno.local/x", "https://evilgoogleapis.com/x",
                         "https://fcm.googleapis.com.evil.com/x", "ftp://fcm.googleapis.com/x"):
            corpo = '{"subscription": {"endpoint": "%s", "keys": {"auth": "a", "p256dh": "b"}}}' % endpoint
            resposta = self.client.post(reverse("notificacao-inscricao-push"), corpo, content_type="application/json")
            self.assertEqual(resposta.status_code, 400, endpoint)
        self.assertEqual(self.op_b.webpush_info.count(), 0)


class LeituraTests(Base):
    def setUp(self):
        super().setUp()
        self.enviar("adm", tipo_destinatario="usuarios", usuarios=[self.op_b.pk], link="/insumo")
        self.item = DestinatarioNotificacao.objects.get()

    def test_abrir_marca_como_lida_com_data_e_segue_o_link(self):
        self.client.login(username="op_b", password=SENHA)
        self.assertIsNone(self.item.lida_em)
        resposta = self.client.get(reverse("notificacao-abrir", args=[self.item.pk]))
        self.assertRedirects(resposta, "/insumo", fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.lida_em)

    def test_nao_le_a_notificacao_de_outro_usuario(self):
        self.client.login(username="gestor_a", password=SENHA)
        self.assertEqual(self.client.get(reverse("notificacao-abrir", args=[self.item.pk])).status_code, 404)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.lida_em)

    def test_ler_uma_notificacao_nao_entregue_conta_como_entregue(self):
        DestinatarioNotificacao.objects.filter(pk=self.item.pk).update(status_entrega="falha", erro_entrega="x")
        self.client.login(username="op_b", password=SENHA)
        self.client.get(reverse("notificacao-abrir", args=[self.item.pk]))
        self.item.refresh_from_db()
        self.assertEqual((self.item.status_entrega, self.item.erro_entrega), ("entregue", ""))

    def test_dropdown_da_navbar_mostra_contador_e_recentes(self):
        self.client.login(username="op_b", password=SENHA)
        resposta = self.client.get(reverse("notificacao-minhas"))
        self.assertEqual(resposta.context["notificacoes_nao_lidas"], 1)
        self.assertContains(resposta, "Ver todas as notificações")
        self.client.get(reverse("notificacao-abrir", args=[self.item.pk]))
        self.assertEqual(self.client.get(reverse("notificacao-minhas")).context["notificacoes_nao_lidas"], 0)

    def test_minhas_notificacoes_filtra_lidas_e_nao_lidas(self):
        self.client.login(username="op_b", password=SENHA)
        self.assertEqual(len(self.client.get(reverse("notificacao-minhas"), {"situacao": "nao-lidas"}).context["itens"]), 1)
        self.client.post(reverse("notificacao-minhas"))  # marcar todas como lidas
        self.assertEqual(len(self.client.get(reverse("notificacao-minhas"), {"situacao": "nao-lidas"}).context["itens"]), 0)
        self.assertEqual(len(self.client.get(reverse("notificacao-minhas"), {"situacao": "lidas"}).context["itens"]), 1)

    def test_anonimo_nao_acessa_a_caixa_de_entrada(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("notificacao-minhas")).status_code, 302)


class HistoricoTests(Base):
    def setUp(self):
        super().setUp()
        self.client.login(username="adm", password=SENHA)

    def itens(self, **filtros):
        return self.client.get(reverse("notificacao-list"), filtros).context["itens"]

    def test_paginacao(self):
        for i in range(12):
            enviar_notificacao(self.requisicao(), {"titulo": f"N{i}", "mensagem": "m", "tipo_destinatario": "todos"})
        resposta = self.client.get(reverse("notificacao-list"))
        self.assertEqual(len(resposta.context["itens"]), 20)
        self.assertTrue(resposta.context["is_paginated"])
        self.assertEqual(len(self.client.get(reverse("notificacao-list"), {"page": 3}).context["itens"]), 8)

    def requisicao(self):
        requisicao = RequestFactory().post("/")
        requisicao.user = self.admin
        return requisicao

    def test_filtros_de_entrega_e_leitura(self):
        enviar_notificacao(self.requisicao(), {"titulo": "A", "mensagem": "m", "tipo_destinatario": "todos"})
        DestinatarioNotificacao.objects.filter(usuario=self.op_b).update(status_entrega="falha")
        DestinatarioNotificacao.objects.filter(usuario=self.gestor_a).update(lida_em="2026-01-01T10:00:00Z")
        self.assertEqual({i.usuario.username for i in self.itens(situacao="nao-entregues")}, {"op_b"})
        self.assertEqual(len(self.itens(situacao="entregues")), 3)
        self.assertEqual({i.usuario.username for i in self.itens(situacao="lidas")}, {"gestor_a"})
        self.assertEqual(len(self.itens(situacao="nao-lidas")), 3)

    def test_filtros_por_usuario_cliente_segmento_periodo_e_texto(self):
        enviar_notificacao(self.requisicao(), {"titulo": "Promoção", "mensagem": "desconto", "tipo_destinatario": "todos"})
        enviar_notificacao(self.requisicao(), {"titulo": "Manutenção", "mensagem": "sistema fora", "tipo_destinatario": "todos"})
        self.assertEqual(len(self.itens(usuario=self.op_b.pk)), 2)
        self.assertEqual({i.usuario.username for i in self.itens(cliente=self.filial_a.pk)}, {"op_filial"})
        self.assertEqual({i.usuario.username for i in self.itens(segmento=Segmento.objects.get(slug="servico").pk)}, {"op_b"})
        self.assertEqual(len(self.itens(q="manut")), 4)
        self.assertEqual(len(self.itens(de="2999-01-01")), 0)
        self.assertEqual(len(self.itens(ate="2000-01-01")), 0)
        self.assertEqual(len(self.itens(de="2000-01-01", ate="2999-01-01")), 8)

    def test_datas_invalidas_nao_geram_erro(self):
        self.assertEqual(self.client.get(reverse("notificacao-list"), {"de": "2026-13-45", "ate": "lixo"}).status_code, 200)

    def test_gestor_so_ve_historico_da_propria_estrutura(self):
        enviar_notificacao(self.requisicao(), {"titulo": "A", "mensagem": "m", "tipo_destinatario": "todos"})
        self.client.login(username="gestor_a", password=SENHA)
        nomes = {i.usuario.username for i in self.itens()}
        self.assertEqual(nomes, {"adm", "gestor_a", "op_filial"})
        self.assertEqual(len(self.itens(usuario=self.op_b.pk)), 0)

    def test_operador_nao_acessa_o_historico(self):
        self.client.login(username="op_b", password=SENHA)
        self.assertEqual(self.client.get(reverse("notificacao-list")).status_code, 403)


class AtualizacaoAutomaticaTests(Base):
    """O sino consulta /notificacoes/resumo: só a caixa do próprio usuário logado."""

    def resumo(self, quem=None):
        if quem:
            self.client.login(username=quem, password=SENHA)
        return self.client.get(reverse("notificacao-resumo"))

    def test_exige_login(self):
        resposta = self.resumo()
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("login", resposta.url)

    def test_nova_notificacao_aparece_sem_recarregar_a_pagina(self):
        self.assertEqual(self.resumo("op_filial").json()["nao_lidas"], 0)
        self.client.logout()
        self.enviar("adm", titulo="Reunião às 15h")
        self.client.logout()
        dados = self.resumo("op_filial").json()
        self.assertEqual(dados["nao_lidas"], 1)
        self.assertIn("Reunião às 15h", dados["html"])
        self.assertNotIn("<script", dados["html"])

    def test_nao_expoe_notificacoes_de_outro_usuario_nem_tenant(self):
        self.enviar("gestor_a", titulo="Só da estrutura A")  # op_b é de outro tenant
        self.client.logout()
        dados = self.resumo("op_b").json()
        self.assertEqual(dados["nao_lidas"], 0)
        self.assertNotIn("Só da estrutura A", dados["html"])
        # parâmetros de usuário na URL são ignorados
        resposta = self.client.get(reverse("notificacao-resumo"), {"usuario": self.gestor_a.pk})
        self.assertNotIn("Só da estrutura A", resposta.json()["html"])

    def test_leitura_atualiza_contador_e_estado(self):
        self.enviar("adm", titulo="Aviso lido")
        self.client.logout()
        self.client.login(username="op_filial", password=SENHA)
        item = DestinatarioNotificacao.objects.get(usuario=self.op_filial)
        self.assertIn("badge-dot", self.resumo().json()["html"])
        self.client.get(reverse("notificacao-abrir", args=[item.pk]))
        dados = self.resumo().json()
        self.assertEqual(dados["nao_lidas"], 0)
        self.assertIn("marked-as-read", dados["html"])

    def test_html_escapa_conteudo_da_notificacao(self):
        self.enviar("adm", titulo="<b>x</b>", mensagem="<script>alert(1)</script>")
        self.client.logout()
        html = self.resumo("op_filial").json()["html"]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_pagina_traz_o_sino_e_o_script(self):
        self.client.login(username="op_filial", password=SENHA)
        resposta = self.client.get(reverse("notificacao-minhas"))
        self.assertContains(resposta, 'id="sino-notificacoes"')
        self.assertContains(resposta, "notificacoes-sino.js")
