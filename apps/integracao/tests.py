"""Integrações: cifra de credenciais, conexão, fila de mensagens com reenvio, webhook (assinatura + idempotência), adaptadores (com a rede
simulada), sincronização de agenda, OAuth (estado), permissões e isolamento entre unidades. NENHUM teste fala com a rede real."""
import hashlib
import hmac
import json
from datetime import date, datetime, time, timedelta, timezone as tz
from decimal import Decimal
from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.financeiro.models import CategoriaFinanceira, ContaPagar, ContaReceber
from apps.notificacao.models import Notificacao
from web_project.regras import RegraNegocioError

from . import cripto, rede, services as s
from .adaptadores import AdaptadorTeste, ErroIntegracao, obter
from .adaptadores.agenda import GoogleCalendar, ICloudCalendar, _ler_vevent
from .adaptadores.base import hmac_sha256
from .adaptadores.instagram import InstagramGraph
from .adaptadores.whatsapp import WhatsAppCloud, WhatsAppIntermediario
from .models import Compromisso, Conexao, EventoWebhook, ItemSincronizado, LogIntegracao, Mensagem

V1 = "/api/v1"
CHAVE = Fernet.generate_key().decode()
SEGREDO = "segredo-super-secreto-123"
cfg = override_settings(INTEGRACAO_CRYPTO_KEYS=CHAVE)


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = cliente
    u.profile.save()
    u.groups.add(Group.objects.get(name=grupo))
    return u


class Req:
    def __init__(self, user):
        self.user, self.META = user, {}


@cfg
class Base(TestCase):
    def setUp(self):
        cache.clear()
        AdaptadorTeste.limpar()
        call_command("seed_acl", verbosity=0)
        self.cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.outro = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        self.gestor = usuario("gestor", self.cliente, "Gestor")
        self.operador = usuario("operador", self.cliente, "Operador")
        self.gestor_b = usuario("gestor_b", self.outro, "Gestor")
        self.req = Req(self.gestor)

    def conexao(self, cliente=None, nome="Teste", config=None, **kw):
        return s.conectar(self.req if cliente in (None, self.cliente) else Req(self.gestor_b), cliente or self.cliente, "teste", nome, {"segredo": SEGREDO}, config or {}, **kw)


class CriptoTests(SimpleTestCase):
    @cfg
    def test_cifra_e_decifra_sem_texto_claro(self):
        texto = cripto.cifrar({"token": "abc123", "k": "v"})
        self.assertNotIn("abc123", texto)
        self.assertEqual(cripto.decifrar(texto), {"token": "abc123", "k": "v"})
        self.assertEqual(cripto.decifrar(""), {})

    def test_chave_errada_nao_decifra_e_rotacao(self):
        antiga, nova = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_settings(INTEGRACAO_CRYPTO_KEYS=antiga):
            cifrado = cripto.cifrar({"t": "1"})
        with override_settings(INTEGRACAO_CRYPTO_KEYS=nova):
            with self.assertRaises(ValueError):
                cripto.decifrar(cifrado)
        with override_settings(INTEGRACAO_CRYPTO_KEYS=f"{nova},{antiga}"):  # rotação: chave nova na frente, antiga ainda decifra
            self.assertEqual(cripto.decifrar(cifrado), {"t": "1"})
            recifrado = cripto.recifrar(cifrado)
        with override_settings(INTEGRACAO_CRYPTO_KEYS=nova):
            self.assertEqual(cripto.decifrar(recifrado), {"t": "1"})

    @override_settings(INTEGRACAO_CRYPTO_KEYS="", DEBUG=False)
    def test_producao_sem_chave_recusa(self):
        with self.assertRaises(cripto.CriptoIndisponivel):
            cripto.cifrar({"a": "b"})

    @override_settings(INTEGRACAO_CRYPTO_KEYS="", DEBUG=True)
    def test_desenvolvimento_deriva_da_secret_key(self):
        self.assertEqual(cripto.decifrar(cripto.cifrar({"a": "b"})), {"a": "b"})


class RedeTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_so_https_e_host_publico(self):
        with self.assertRaises(ValueError):
            rede.url_segura("http://exemplo.com/x")
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("10.0.0.5", 443))]):
            with self.assertRaises(ValueError):
                rede.url_segura("https://interno.exemplo.com")
        for ip in ("127.0.0.1", "169.254.169.254", "192.168.1.1", "::1"):
            with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                with self.assertRaises(ValueError, msg=ip):
                    rede.url_segura("https://x.exemplo.com")
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
            self.assertEqual(rede.url_segura("https://publico.exemplo.com/a"), "https://publico.exemplo.com/a")
        with self.assertRaises(ValueError):
            rede.url_segura("https://user:pw@publico.exemplo.com")

    @override_settings(DEBUG=True)
    def test_localhost_so_em_debug(self):
        self.assertEqual(rede.url_segura("http://localhost:9000/x"), "http://localhost:9000/x")


class ConexaoTests(Base):
    def test_conecta_cifra_testa_e_audita_sem_segredo(self):
        c = self.conexao()
        self.assertEqual((c.status, c.cliente_id), ("conectada", self.cliente.pk))
        bruto = Conexao.objects.filter(pk=c.pk).values_list("credenciais_cifradas", flat=True)[0]
        self.assertNotIn(SEGREDO, bruto)
        self.assertEqual(s.credenciais_de(c)["segredo"], SEGREDO)
        textos = " ".join(f"{r.descricao} {r.alteracoes} {r.registro_nome}" for r in RegistroAuditoria.objects.filter(modulo="integracao"))
        self.assertNotIn(SEGREDO, textos)
        self.assertNotIn(SEGREDO, " ".join(l.detalhe for l in LogIntegracao.objects.all()))

    def test_validacoes(self):
        with self.assertRaises(RegraNegocioError):
            s.conectar(self.req, self.cliente, "teste", "X", {}, {})  # falta o segredo
        with self.assertRaises(RegraNegocioError):
            s.conectar(self.req, self.cliente, "teste", " ", {"segredo": "a"}, {})
        with self.assertRaises(ErroIntegracao):
            obter("inexistente")
        self.conexao(nome="A")
        with self.assertRaises(RegraNegocioError):
            self.conexao(nome="A")  # nome repetido na unidade
        self.conexao(self.outro, nome="A")  # outra unidade pode

    def test_teste_com_falha_deixa_em_erro_sem_vazar(self):
        c = self.conexao(config={"falhar_teste": "1"})
        self.assertEqual(c.status, "erro")
        self.assertIn("Falha simulada", c.ultimo_erro)
        self.assertTrue(LogIntegracao.objects.filter(conexao=c, nivel="erro").exists())

    def test_trocar_credenciais_mantem_o_que_nao_veio(self):
        c = self.conexao()
        s.atualizar_credenciais(self.req, c, {"segredo": "novo-segredo"})
        self.assertEqual(s.credenciais_de(c)["segredo"], "novo-segredo")
        s.atualizar_credenciais(self.req, c, {"segredo": ""})  # vazio mantém
        self.assertEqual(s.credenciais_de(c)["segredo"], "novo-segredo")
        registro = RegistroAuditoria.objects.filter(modulo="integracao", descricao__contains="substituídas").latest("id")
        self.assertNotIn("novo-segredo", registro.descricao)

    def test_desconectar_apaga_credenciais_cancela_fila_e_regras_de_exclusao(self):
        c = self.conexao()
        m, _ = s.enfileirar_mensagem(self.req, c, "5511999990000", "oi")
        s.desconectar(self.req, c)
        c.refresh_from_db()
        m.refresh_from_db()
        self.assertEqual((c.status, c.credenciais_cifradas, m.status), ("revogada", "", "cancelada"))
        with self.assertRaises(RegraNegocioError):
            s.desconectar(self.req, c)
        with self.assertRaises(RegraNegocioError):
            s.excluir_conexao(self.req, c)  # tem mensagem no histórico
        limpa = self.conexao(nome="Limpa")
        with self.assertRaises(RegraNegocioError):
            s.excluir_conexao(self.req, limpa)  # ainda ativa
        s.desconectar(self.req, limpa)
        s.excluir_conexao(self.req, limpa)
        self.assertFalse(Conexao.objects.filter(pk=limpa.pk).exists())

    def test_renovar_credenciais(self):
        c = self.conexao(config={"renovavel": "1"})
        self.assertTrue(s.renovar_credenciais(c))
        c.refresh_from_db()
        self.assertTrue(s.credenciais_de(c)["token"].startswith("novo-"))
        self.assertIsNotNone(c.expira_em)
        self.assertFalse(s.renovar_credenciais(self.conexao(nome="Sem renovacao")))  # provedor sem renovação


class MensagensTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.conexao()

    def test_enfileira_idempotente_e_valida(self):
        a, c1 = s.enfileirar_mensagem(self.req, self.c, "(11) 99999-0000", "Olá", chave="k1")
        b, c2 = s.enfileirar_mensagem(self.req, self.c, "(11) 99999-0000", "Olá", chave="k1")
        self.assertEqual((c1, c2, a.pk, a.contato), (True, False, b.pk, "11999990000"))
        for contato, texto in (("12", "x"), ("11999990000", ""), ("11999990000", "x" * 2001)):
            with self.assertRaises(RegraNegocioError):
                s.enfileirar_mensagem(self.req, self.c, contato, texto)
        registro = RegistroAuditoria.objects.filter(modulo="integracao", tabela="integracao_mensagem").latest("id")
        self.assertNotIn("Olá", registro.descricao)
        self.assertNotIn("11999990000", registro.descricao)

    def test_conexao_inativa_nao_envia(self):
        s.desconectar(self.req, self.c)
        with self.assertRaises(RegraNegocioError):
            s.enfileirar_mensagem(self.req, self.c, "11999990000", "oi")

    def test_processa_fila_e_marca_enviada(self):
        s.enfileirar_mensagem(self.req, self.c, "11999990000", "Pedido pronto")
        r = s.processar_fila()
        self.assertEqual(r["enviadas"], 1)
        m = Mensagem.objects.get()
        self.assertEqual((m.status, m.tentativas, bool(m.enviado_em)), ("enviada", 1, True))
        self.assertEqual(AdaptadorTeste.ENVIADAS, [("11999990000", "Pedido pronto")])
        self.assertEqual(s.processar_fila()["enviadas"], 0)  # não reenvia

    def test_falha_temporaria_reagenda_com_espera_crescente_e_desiste(self):
        c = self.conexao(nome="Instavel", config={"falhar_envio": "temporario"})
        m, _ = s.enfileirar_mensagem(self.req, c, "11999990000", "oi")
        agora = timezone.now()
        for tentativa in range(1, s.MAX_TENTATIVAS):
            s.processar_fila(agora=agora)
            m.refresh_from_db()
            self.assertEqual((m.status, m.tentativas), ("pendente", tentativa))
            self.assertGreater(m.proxima_tentativa, agora)
            agora = m.proxima_tentativa + timedelta(seconds=1)
        s.processar_fila(agora=agora)
        m.refresh_from_db()
        self.assertEqual((m.status, m.tentativas), ("erro", s.MAX_TENTATIVAS))

    def test_nao_processa_antes_da_hora(self):
        c = self.conexao(nome="Instavel2", config={"falhar_envio": "temporario"})
        m, _ = s.enfileirar_mensagem(self.req, c, "11999990000", "oi")
        s.processar_fila()
        self.assertEqual(s.processar_fila()["adiadas"], 0)  # ainda não chegou a próxima tentativa
        m.refresh_from_db()
        self.assertEqual(m.tentativas, 1)

    def test_falha_definitiva_nao_repete(self):
        c = self.conexao(nome="Ruim", config={"falhar_envio": "definitivo"})
        m, _ = s.enfileirar_mensagem(self.req, c, "11999990000", "oi")
        r = s.processar_fila()
        m.refresh_from_db()
        self.assertEqual((r["falhas"], m.status, m.tentativas), (1, "erro", 1))

    def test_limite_por_minuto_adia(self):
        with mock.patch.object(s, "LIMITE_ENVIOS_POR_MINUTO", 2):
            for i in range(4):
                s.enfileirar_mensagem(self.req, self.c, "11999990000", f"m{i}")
            r = s.processar_fila()
        self.assertEqual((r["enviadas"], r["adiadas"]), (2, 2))

    def test_conexao_desconectada_nao_envia(self):
        s.enfileirar_mensagem(self.req, self.c, "11999990000", "oi")
        Conexao.objects.filter(pk=self.c.pk).update(status="erro")
        self.assertEqual(s.processar_fila()["enviadas"], 0)


class WebhookTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.conexao()
        self.url = reverse("integracao-webhook", args=[self.c.webhook_token])

    def enviar(self, eventos, segredo=SEGREDO, token=None, **extra):
        corpo = json.dumps({"eventos": eventos}).encode()
        assinatura = hmac_sha256(segredo, corpo)
        url = reverse("integracao-webhook", args=[token or self.c.webhook_token])
        return self.client.post(url, corpo, content_type="application/json", HTTP_X_TESTE_ASSINATURA=assinatura, **extra)

    def test_recebe_guarda_e_notifica_a_equipe(self):
        r = self.enviar([{"id": "e1", "de": "5511999990000", "texto": "Quero 2 bolos"}])
        self.assertEqual(r.status_code, 200)
        m = Mensagem.objects.get()
        self.assertEqual((m.direcao, m.status, m.texto, m.id_externo), ("entrada", "recebida", "Quero 2 bolos", "e1"))
        self.assertEqual(Notificacao.objects.count(), 1)
        self.assertNotIn("Quero 2 bolos", Notificacao.objects.get().mensagem)  # a notificação não leva o conteúdo
        self.assertTrue(LogIntegracao.objects.filter(conexao=self.c, evento="webhook").exists())

    def test_idempotente_nao_duplica_nem_renotifica(self):
        self.enviar([{"id": "e1", "de": "1", "texto": "a"}])
        self.enviar([{"id": "e1", "de": "1", "texto": "a"}, {"id": "e2", "de": "1", "texto": "b"}])
        self.assertEqual(Mensagem.objects.count(), 2)
        self.assertEqual(EventoWebhook.objects.count(), 2)

    def test_assinatura_invalida_ou_ausente_nada_e_processado(self):
        self.assertEqual(self.enviar([{"id": "e1", "texto": "x"}], segredo="errado").status_code, 403)
        corpo = json.dumps({"eventos": [{"id": "e2"}]}).encode()
        self.assertEqual(self.client.post(self.url, corpo, content_type="application/json").status_code, 403)
        self.assertFalse(Mensagem.objects.exists())
        self.assertTrue(LogIntegracao.objects.filter(conexao=self.c, nivel="aviso", evento="webhook").exists())

    def test_token_desconhecido_inativa_e_corpo_grande(self):
        self.assertEqual(self.enviar([], token="inexistente").status_code, 404)
        corpo = b"x" * (s.MAX_CORPO_WEBHOOK + 1)
        self.assertEqual(self.client.post(self.url, corpo, content_type="application/json", HTTP_X_TESTE_ASSINATURA=hmac_sha256(SEGREDO, corpo)).status_code, 413)
        s.desconectar(self.req, self.c)
        self.assertEqual(self.enviar([{"id": "e9"}]).status_code, 404)

    def test_json_invalido_e_desafio_de_verificacao(self):
        corpo = b"nao-json"
        self.assertEqual(self.client.post(self.url, corpo, content_type="application/json", HTTP_X_TESTE_ASSINATURA=hmac_sha256(SEGREDO, corpo)).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"desafio": "abc"}).content, b"abc")

    def test_rate_limit_por_conexao_e_ip(self):
        with mock.patch("apps.integracao.views.LIMITE_WEBHOOK", (3, 60)):
            codigos = [self.enviar([{"id": f"r{i}"}]).status_code for i in range(5)]
        self.assertEqual(codigos[:3], [200, 200, 200])
        self.assertEqual(codigos[-1], 429)

    def test_sem_login_e_sem_csrf(self):
        self.assertEqual(self.enviar([{"id": "z1", "de": "1", "texto": "t"}]).status_code, 200)  # público: autentica a assinatura

    def test_mensagens_recebidas_nao_vazam_para_outra_unidade(self):
        self.enviar([{"id": "e1", "de": "1", "texto": "segredo da empresa A"}])
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("integracao-mensagens", args=[self.c.pk])).status_code, 404)


class AdaptadoresTests(SimpleTestCase):
    def conexao(self, **config):
        return mock.Mock(config=config)

    def test_whatsapp_cloud_assinatura_desafio_e_payload(self):
        a, c = WhatsAppCloud(), self.conexao(phone_number_id="123")
        cred = {"access_token": "t", "app_secret": "sec", "verify_token": "vt"}
        corpo = b'{"entry":[]}'
        assinatura = "sha256=" + hmac_sha256("sec", corpo)
        self.assertTrue(a.assinatura_valida(c, cred, {"X-Hub-Signature-256": assinatura}, corpo))
        self.assertFalse(a.assinatura_valida(c, cred, {"X-Hub-Signature-256": "sha256=00"}, corpo))
        self.assertFalse(a.assinatura_valida(c, {"app_secret": ""}, {}, corpo))
        self.assertEqual(a.desafio_webhook(c, cred, {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"}), "42")
        self.assertIsNone(a.desafio_webhook(c, cred, {"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "42"}))
        payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.1", "from": "5511999990000", "timestamp": "1", "text": {"body": "Oi"}}]}}]}]}
        self.assertEqual(a.interpretar_webhook(c, payload), [{"id": "wamid.1", "tipo": "mensagem", "de": "5511999990000", "texto": "Oi", "quando": "1"}])

    def test_whatsapp_cloud_envio_e_mapeamento_de_erros(self):
        a, c = WhatsAppCloud(), self.conexao(phone_number_id="123")
        cred = {"access_token": "TOKEN-SECRETO"}
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"messages": [{"id": "wamid.9"}]})) as req:
            self.assertEqual(a.enviar_mensagem(c, cred, "5511999990000", "oi"), "wamid.9")
        metodo, url, cab, corpo = req.call_args[0][:4]
        self.assertEqual((metodo, url), ("POST", "https://graph.facebook.com/v21.0/123/messages"))
        self.assertEqual(corpo["to"], "5511999990000")
        with mock.patch.object(rede, "requisitar", side_effect=rede.ErroDeRede("HTTP 429", status=429, espera=30, temporario=True)):
            with self.assertRaises(ErroIntegracao) as erro:
                a.enviar_mensagem(c, cred, "55", "oi")
        self.assertTrue(erro.exception.temporario)
        self.assertEqual(erro.exception.espera, 30)
        self.assertNotIn("TOKEN-SECRETO", str(erro.exception))
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"x": 1})):
            with self.assertRaises(ErroIntegracao):
                a.enviar_mensagem(c, cred, "55", "oi")

    def test_intermediario_bloqueia_url_interna_e_valida_assinatura(self):
        a = WhatsAppIntermediario()
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("10.1.1.1", 443))]):
            with self.assertRaises(ErroIntegracao):
                a.validar({"token": "t", "segredo_webhook": "s"}, {"base_url": "https://interno.exemplo.com"})
        c = self.conexao(base_url="https://api.exemplo.com")
        corpo = b'{"id":"1","de":"55","texto":"oi"}'
        self.assertTrue(a.assinatura_valida(c, {"segredo_webhook": "s"}, {"X-Assinatura": "sha256=" + hmac_sha256("s", corpo)}, corpo))
        self.assertFalse(a.assinatura_valida(c, {"segredo_webhook": "s"}, {}, corpo))
        self.assertEqual(a.interpretar_webhook(c, {"id": "1", "de": "55", "texto": "oi"})[0]["texto"], "oi")
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]), mock.patch.object(rede, "requisitar", return_value=(200, {}, {"id": "m1"})) as req:
            self.assertEqual(a.enviar_mensagem(c, {"token": "t"}, "55", "oi"), "m1")
        self.assertEqual(req.call_args[0][1], "https://api.exemplo.com/mensagens")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="csec")
    def test_google_oauth_escopo_minimo_troca_renova_e_revoga(self):
        g = GoogleCalendar()
        self.assertTrue(g.configurado())
        url = g.url_autorizacao("estado123", "https://app.exemplo.com/retorno")
        self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events", url)
        self.assertIn("state=estado123", url)
        self.assertNotIn("csec", url)
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})):
            cred, expira = g.trocar_codigo("codigo", "https://app.exemplo.com/retorno")
        self.assertEqual((cred["access_token"], cred["refresh_token"]), ("AT", "RT"))
        self.assertGreater(expira, datetime.now(tz.utc))
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"access_token": "AT2", "expires_in": 100})):
            nova, _ = g.renovar(mock.Mock(config={}), cred)
        self.assertEqual((nova["access_token"], nova["refresh_token"]), ("AT2", "RT"))
        with self.assertRaises(ErroIntegracao):
            g.renovar(mock.Mock(config={}), {"access_token": "x"})
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, b"")) as req:
            g.revogar(mock.Mock(config={}), cred)
        self.assertIn("revoke", req.call_args[0][1])

    def test_google_evento_dia_inteiro_e_listagem(self):
        g, c = GoogleCalendar(), self.conexao(calendar_id="primary")
        evento = {"titulo": "Pagar: Luz", "descricao": "d", "inicio": datetime(2026, 10, 20, tzinfo=tz.utc), "fim": datetime(2026, 10, 20, 23, 59, tzinfo=tz.utc), "todo_o_dia": True}
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"id": "g1", "etag": "e"})) as req:
            self.assertEqual(g.criar_evento(c, {"access_token": "t"}, evento), ("g1", "e"))
        corpo = req.call_args[0][3]
        self.assertEqual((corpo["start"], corpo["end"]), ({"date": "2026-10-20"}, {"date": "2026-10-21"}))  # fim exclusivo
        dados = {"items": [{"id": "g1", "summary": "X", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}, "updated": "2026-10-01T10:00:00Z", "etag": "e"}]}
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, dados)):
            itens = g.listar_eventos(c, {"access_token": "t"}, datetime.now(tz.utc), datetime.now(tz.utc))
        self.assertEqual((itens[0]["id"], itens[0]["todo_o_dia"]), ("g1", True))

    def test_icloud_ics_e_leitura(self):
        a = ICloudCalendar()
        evento = {"titulo": "Entrega, bairro; X", "descricao": "linha1\nlinha2", "inicio": datetime(2026, 10, 20, 15, 0, tzinfo=tz.utc), "fim": datetime(2026, 10, 20, 16, 0, tzinfo=tz.utc), "todo_o_dia": False}
        ics = a._ics("uid-1", evento)
        self.assertIn("UID:uid-1", ics)
        self.assertIn("SUMMARY:Entrega\\, bairro\\; X", ics)
        self.assertIn("DTSTART:20261020T150000Z", ics)
        bloco = "BEGIN:VEVENT" + ics.split("BEGIN:VEVENT", 1)[1].split("END:VEVENT")[0] + "END:VEVENT"
        lido = _ler_vevent(bloco)
        self.assertEqual((lido["id"], lido["titulo"], lido["descricao"]), ("uid-1", "Entrega, bairro; X", "linha1\nlinha2"))
        with mock.patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("10.0.0.1", 443))]):
            with self.assertRaises(ErroIntegracao):
                a.validar({"usuario": "u", "senha_app": "p"}, {"calendario_url": "https://interno.exemplo.com/cal"})
        cab = a._cab({"usuario": "u@x.com", "senha_app": "abcd"})
        self.assertTrue(cab["Authorization"].startswith("Basic "))

    def test_instagram_webhook(self):
        a, c = InstagramGraph(), self.conexao(ig_user_id="9")
        cred = {"app_secret": "sec", "verify_token": "vt"}
        corpo = b"{}"
        self.assertTrue(a.assinatura_valida(c, cred, {"X-Hub-Signature-256": "sha256=" + hmac_sha256("sec", corpo)}, corpo))
        payload = {"entry": [{"time": 5, "messaging": [{"sender": {"id": "77"}, "timestamp": 1, "message": {"mid": "m1", "text": "oi"}}],
                              "changes": [{"field": "comments", "value": {"id": "c1", "text": "lindo", "from": {"id": "88"}}}]}]}
        eventos = a.interpretar_webhook(c, payload)
        self.assertEqual([(e["id"], e["tipo"]) for e in eventos], [("m1", "mensagem"), ("c1", "comentario")])
        with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"message_id": "mid.9"})):
            self.assertEqual(a.enviar_mensagem(c, {"access_token": "t"}, "77", "obrigado"), "mid.9")


class SincronizacaoTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.conexao()
        self.categoria = CategoriaFinanceira.objects.create(cliente=self.cliente, nome="Geral", tipo="despesa")
        self.hoje = timezone.localdate()

    def conta(self, descricao="Luz", dias=5, valor=5000, cliente=None, modelo=ContaPagar):
        categoria = self.categoria if cliente in (None, self.cliente) else CategoriaFinanceira.objects.create(cliente=cliente, nome="G", tipo="despesa")
        return modelo.objects.create(cliente=cliente or self.cliente, descricao=descricao, contraparte="Forn", categoria=categoria, valor=valor, vencimento=self.hoje + timedelta(days=dias))

    def compromisso(self, titulo="Reserva Silva", horas=48, **kw):
        inicio = timezone.now() + timedelta(hours=horas)
        return Compromisso.objects.create(cliente=self.cliente, titulo=titulo, tipo="reserva", inicio=inicio, fim=inicio + timedelta(hours=2), **kw)

    def test_publica_vencimentos_e_compromissos_sem_valor_por_padrao(self):
        self.conta("Luz")
        self.conta("Aluguel", modelo=ContaReceber)
        self.compromisso()
        r = s.sincronizar_agenda(self.c)
        self.assertEqual(r["criados"], 3)
        titulos = sorted(e["titulo"] for e in AdaptadorTeste.EVENTOS.values())
        self.assertEqual(titulos, ["Pagar: Luz", "Receber: Aluguel", "Reserva Silva"])
        self.assertFalse(any("R$" in t for t in titulos))  # valor financeiro não vai para o provedor sem opt-in
        self.c.refresh_from_db()
        self.assertIsNotNone(self.c.ultima_sincronizacao)

    def test_opt_in_de_valores(self):
        self.conta("Luz", valor=12345)
        c = self.conexao(nome="Com valor", config={"incluir_valores": "1"})
        s.sincronizar_agenda(c)
        self.assertTrue(any("R$ 123,45" in e["titulo"] for e in AdaptadorTeste.EVENTOS.values()))

    def test_segunda_execucao_sem_mudancas_nao_faz_nada(self):
        self.conta()
        self.compromisso()
        s.sincronizar_agenda(self.c)
        r = s.sincronizar_agenda(self.c)
        self.assertEqual((r["criados"], r["atualizados"], r["removidos"], r["importados"], r["puxados"]), (0, 0, 0, 0, 0))
        self.assertEqual(len(AdaptadorTeste.EVENTOS), 2)

    def test_mudanca_local_atualiza_e_conta_paga_sai_da_agenda(self):
        conta = self.conta("Luz")
        comp = self.compromisso()
        s.sincronizar_agenda(self.c)
        Compromisso.objects.filter(pk=comp.pk).update(titulo="Reserva Souza")
        ContaPagar.objects.filter(pk=conta.pk).update(valor_baixado=conta.valor)
        r = s.sincronizar_agenda(self.c)
        self.assertEqual((r["atualizados"], r["removidos"]), (1, 1))
        self.assertEqual([e["titulo"] for e in AdaptadorTeste.EVENTOS.values()], ["Reserva Souza"])
        self.assertFalse(ItemSincronizado.objects.filter(tipo_local="conta_pagar").exists())

    def test_compromisso_desativado_ou_conta_cancelada_sai(self):
        conta = self.conta()
        comp = self.compromisso()
        s.sincronizar_agenda(self.c)
        Compromisso.objects.filter(pk=comp.pk).update(ativo=False)
        ContaPagar.objects.filter(pk=conta.pk).update(ativo=False)
        s.sincronizar_agenda(self.c)
        self.assertEqual(AdaptadorTeste.EVENTOS, {})

    def test_so_a_propria_unidade_e_so_dentro_da_janela(self):
        self.conta("Da outra", cliente=self.outro)
        self.conta("Distante", dias=200)
        self.conta("Minha")
        s.sincronizar_agenda(self.c)
        self.assertEqual([e["titulo"] for e in AdaptadorTeste.EVENTOS.values()], ["Pagar: Minha"])

    def test_importa_evento_criado_no_calendario(self):
        AdaptadorTeste.EVENTOS["ext-1"] = {"id": "ext-1", "titulo": "Reunião no banco", "descricao": "", "inicio": datetime.now(tz.utc) + timedelta(days=2), "fim": datetime.now(tz.utc) + timedelta(days=2, hours=1),
                                           "todo_o_dia": False, "atualizado_em": datetime.now(tz.utc), "etag": "1"}
        r = s.sincronizar_agenda(self.c)
        self.assertEqual(r["importados"], 1)
        c = Compromisso.objects.get(titulo="Reunião no banco")
        self.assertEqual((c.origem, c.cliente_id), ("externa", self.cliente.pk))
        self.assertEqual(s.sincronizar_agenda(self.c)["importados"], 0)  # não importa de novo

    def test_conflito_edicao_externa_mais_recente_vence_e_local_mais_novo_vence(self):
        comp = self.compromisso("Original")
        s.sincronizar_agenda(self.c)
        item = ItemSincronizado.objects.get(tipo_local="compromisso")
        # externo editado DEPOIS do local -> o sistema adota a versão externa
        AdaptadorTeste.EVENTOS[item.id_externo]["titulo"] = "Editado no calendário"
        AdaptadorTeste.EVENTOS[item.id_externo]["atualizado_em"] = timezone.now() + timedelta(minutes=5)
        r = s.sincronizar_agenda(self.c)
        comp.refresh_from_db()
        self.assertEqual((r["puxados"], comp.titulo), (1, "Editado no calendário"))
        self.assertEqual(AdaptadorTeste.EVENTOS[item.id_externo]["titulo"], "Editado no calendário")
        # local editado DEPOIS -> sobrescreve o externo
        Compromisso.objects.filter(pk=comp.pk).update(titulo="Local mais novo", atualizado_em=timezone.now() + timedelta(minutes=10))
        AdaptadorTeste.EVENTOS[item.id_externo]["titulo"] = "Externo antigo"
        AdaptadorTeste.EVENTOS[item.id_externo]["atualizado_em"] = timezone.now() - timedelta(minutes=30)
        s.sincronizar_agenda(self.c)
        self.assertEqual(AdaptadorTeste.EVENTOS[item.id_externo]["titulo"], "Local mais novo")

    def test_edicao_externa_de_vencimento_e_sobrescrita(self):
        self.conta("Luz")
        s.sincronizar_agenda(self.c)
        item = ItemSincronizado.objects.get(tipo_local="conta_pagar")
        AdaptadorTeste.EVENTOS[item.id_externo]["titulo"] = "Alterado por fora"
        s.sincronizar_agenda(self.c)
        self.assertEqual(ContaPagar.objects.get().descricao, "Luz")  # o sistema é a fonte da verdade dos vencimentos

    def test_evento_de_compromisso_removido_no_calendario_desativa_o_local(self):
        comp = self.compromisso()
        s.sincronizar_agenda(self.c)
        AdaptadorTeste.EVENTOS.clear()
        r = s.sincronizar_agenda(self.c)
        comp.refresh_from_db()
        self.assertEqual((r["removidos"], comp.ativo), (1, False))

    def test_limite_de_operacoes_por_execucao(self):
        for i in range(5):
            self.conta(f"C{i}")
        r = s.sincronizar_agenda(self.c, limite=3)
        self.assertEqual(r["criados"], 3)
        self.assertEqual(s.sincronizar_agenda(self.c, limite=3)["criados"], 2)

    def test_erro_temporario_pausa_e_definitivo_marca_erro_e_notifica(self):
        self.conta()
        c = self.conexao(nome="Instavel", config={"falhar_agenda": "1"})
        r = s.sincronizar_agenda(c)
        c.refresh_from_db()
        self.assertEqual((r["erros"], c.status), (1, "conectada"))
        self.assertIn("pausa_ate", c.config)
        self.assertEqual(s.sincronizar_agenda(c)["erros"], 0)  # em pausa: nem tenta
        with mock.patch.object(AdaptadorTeste, "criar_evento", side_effect=ErroIntegracao("Credencial revogada", temporario=False)):
            c2 = self.conexao(nome="Revogada")
            s.sincronizar_agenda(c2)
        c2.refresh_from_db()
        self.assertEqual(c2.status, "erro")
        self.assertTrue(Notificacao.objects.filter(titulo__contains="Revogada").exists())

    def test_renova_credencial_antes_de_expirar(self):
        c = self.conexao(nome="Renova", config={"renovavel": "1"})
        Conexao.objects.filter(pk=c.pk).update(expira_em=timezone.now() + timedelta(minutes=1))
        c.refresh_from_db()
        s.sincronizar_agenda(c)
        c.refresh_from_db()
        self.assertTrue(s.credenciais_de(c)["token"].startswith("novo-"))

    def test_nao_sincroniza_conexao_inativa_nem_de_mensagem(self):
        self.conta()
        s.desconectar(self.req, self.c)
        self.assertEqual(s.sincronizar_agenda(self.c)["criados"], 0)


class TelasEPermissoesTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.conexao()

    def test_operador_nao_acessa_integracoes_mas_usa_a_agenda(self):
        self.client.force_login(self.operador)
        for nome, args in (("integracoes", []), ("integracao-detalhe", [self.c.pk]), ("integracao-mensagens", [self.c.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 403, nome)
        self.assertEqual(self.client.post(reverse("integracao-acao", args=[self.c.pk, "desconectar"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("compromisso-list")).status_code, 200)
        inicio = (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        fim = (timezone.localtime() + timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M")
        r = self.client.post(reverse("compromisso-create"), {"tipo": "reserva", "titulo": "Mesa 4", "inicio": inicio, "fim": fim, "ativo": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Compromisso.objects.get().cliente_id, self.cliente.pk)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="integracao", acao="criar", tabela="integracao_compromisso").exists())

    def test_gestor_ve_estado_mas_nunca_a_credencial(self):
        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("integracao-detalhe", args=[self.c.pk])).content.decode()
        self.assertIn("guardadas cifradas", pagina)
        self.assertNotIn(SEGREDO, pagina)
        self.assertNotIn(self.c.credenciais_cifradas, pagina)
        form = self.client.get(reverse("integracao-credenciais", args=[self.c.pk])).content.decode()
        self.assertNotIn(SEGREDO, form)
        self.assertEqual(self.client.get(reverse("integracoes")).status_code, 200)

    @override_settings(DEBUG=True)
    def test_conectar_pela_tela_credencial_em_campo_de_senha(self):
        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("integracao-nova", args=["teste"])).content.decode()
        self.assertIn('type="password"', pagina)
        r = self.client.post(reverse("integracao-nova", args=["teste"]), {"nome": "Pela tela", "cred_segredo": "s3gredo-tela", "notificar_usuarios": "on"})
        self.assertEqual(r.status_code, 302)
        conexao = Conexao.objects.get(nome="Pela tela")
        self.assertEqual((conexao.cliente_id, conexao.status, s.credenciais_de(conexao)["segredo"]), (self.cliente.pk, "conectada", "s3gredo-tela"))
        self.assertNotIn("s3gredo-tela", self.client.get(reverse("integracao-detalhe", args=[conexao.pk])).content.decode())

    def test_provedor_de_teste_nao_aparece_em_producao(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("integracao-nova", args=["teste"])).status_code, 404)
        self.assertNotIn("Provedor de teste", self.client.get(reverse("integracoes")).content.decode())

    def test_acoes_testar_sincronizar_desconectar_e_tenant(self):
        self.client.force_login(self.gestor)
        self.client.post(reverse("integracao-acao", args=[self.c.pk, "testar"]))
        self.client.post(reverse("integracao-acao", args=[self.c.pk, "sincronizar"]))
        self.client.force_login(self.gestor_b)
        for nome, args in (("integracao-detalhe", [self.c.pk]), ("integracao-credenciais", [self.c.pk]), ("integracao-mensagens", [self.c.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 404, nome)
        self.assertEqual(self.client.post(reverse("integracao-acao", args=[self.c.pk, "desconectar"])).status_code, 404)
        self.assertNotIn("Teste", self.client.get(reverse("integracoes")).content.decode())
        self.client.force_login(self.gestor)
        self.client.post(reverse("integracao-acao", args=[self.c.pk, "desconectar"]))
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.credenciais_cifradas), ("revogada", ""))
        self.assertEqual(self.client.post(reverse("integracao-acao", args=[self.c.pk, "xyz"])).status_code, 404)

    def test_enviar_mensagem_pela_tela_exige_permissao(self):
        self.client.force_login(self.gestor)
        self.client.post(reverse("integracao-mensagens", args=[self.c.pk]), {"contato": "11999990000", "texto": "Olá"})
        self.assertEqual(Mensagem.objects.filter(direcao="saida").count(), 1)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.post(reverse("integracao-mensagens", args=[self.c.pk]), {"contato": "11999990000", "texto": "x"}).status_code, 403)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET="")
    def test_google_nao_configurado_avisa(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("integracao-google-iniciar"), follow=True)
        self.assertContains(r, "GOOGLE_OAUTH_CLIENT_ID")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="csec")
    def test_google_oauth_estado_assinado_de_uso_unico(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("integracao-google-iniciar"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("accounts.google.com", r["Location"])
        estado = r["Location"].split("state=")[1].split("&")[0]
        from urllib.parse import unquote

        estado = unquote(estado)
        # estado adulterado / de outra sessão: recusado
        r2 = self.client.get(reverse("integracao-google-retorno"), {"state": estado + "x", "code": "abc"}, follow=True)
        self.assertContains(r2, "inválida ou expirada")
        # fluxo correto (a primeira tentativa já consumiu o nonce: refaz o início)
        r = self.client.post(reverse("integracao-google-iniciar"))
        estado = unquote(r["Location"].split("state=")[1].split("&")[0])
        with mock.patch.object(GoogleCalendar, "trocar_codigo", return_value=({"access_token": "AT", "refresh_token": "RT"}, timezone.now() + timedelta(hours=1))), \
                mock.patch.object(GoogleCalendar, "testar", return_value=None):
            r3 = self.client.get(reverse("integracao-google-retorno"), {"state": estado, "code": "abc"})
        self.assertEqual(r3.status_code, 302)
        google = Conexao.objects.get(provedor="google_calendar")
        self.assertEqual((google.cliente_id, google.status, s.credenciais_de(google)["refresh_token"]), (self.cliente.pk, "conectada", "RT"))
        self.assertEqual(google.escopos, "https://www.googleapis.com/auth/calendar.events")
        # o mesmo state não vale duas vezes
        r4 = self.client.get(reverse("integracao-google-retorno"), {"state": estado, "code": "abc"}, follow=True)
        self.assertContains(r4, "inválida ou expirada")
        # state de outro usuário
        self.client.force_login(self.gestor_b)
        r5 = self.client.get(reverse("integracao-google-retorno"), {"state": estado, "code": "abc"}, follow=True)
        self.assertContains(r5, "inválida ou expirada")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="csec")
    def test_google_autorizacao_negada(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("integracao-google-iniciar"))
        from urllib.parse import unquote

        estado = unquote(r["Location"].split("state=")[1].split("&")[0])
        r2 = self.client.get(reverse("integracao-google-retorno"), {"state": estado, "error": "access_denied"}, follow=True)
        self.assertContains(r2, "negada")
        self.assertFalse(Conexao.objects.filter(provedor="google_calendar").exists())


@override_settings(DEBUG=True)
@cfg
class APITests(Base):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o, self.api_b = (self.api(u) for u in (self.gestor, self.operador, self.gestor_b))

    def api(self, user):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=user)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def ok(self, r, codigo=200):
        self.assertEqual(r.status_code, codigo, r.content[:400])
        return r.json().get("data")

    def test_provedores_conectar_e_credencial_nunca_devolvida(self):
        provedores = {p["chave"]: p for p in self.ok(self.api_g.get(f"{V1}/integracoes/provedores/"))}
        self.assertTrue(provedores["whatsapp_cloud"]["credenciais"][0]["secreto"])
        self.assertFalse(provedores["whatsapp_cloud"]["validado"])
        c = self.ok(self.api_g.post(f"{V1}/integracoes/", {"provedor": "teste", "nome": "API", "credenciais": {"segredo": "api-secreto"}}, format="json"), 201)
        self.assertEqual((c["status"], c["validado"]), ("conectada", True))
        self.assertNotIn("api-secreto", json.dumps(c))
        detalhe = self.ok(self.api_g.get(f"{V1}/integracoes/{c['id']}/"))
        self.assertNotIn("credenciais", detalhe)
        self.assertNotIn("api-secreto", json.dumps(detalhe))
        for corpo in ({"provedor": "xyz", "nome": "x", "credenciais": {}}, {"provedor": "google_calendar", "nome": "x", "credenciais": {}}, {"provedor": "teste", "nome": "y", "credenciais": {}}):
            self.assertEqual(self.api_g.post(f"{V1}/integracoes/", corpo, format="json").status_code, 400 if corpo["provedor"] != "teste" else 422, corpo)

    def test_mensagem_idempotente_listagem_e_permissoes(self):
        c = self.ok(self.api_g.post(f"{V1}/integracoes/", {"provedor": "teste", "nome": "M", "credenciais": {"segredo": "s"}}, format="json"), 201)
        corpo = {"conexao": c["id"], "contato": "11999990000", "texto": "Olá"}
        m1 = self.ok(self.api_g.post(f"{V1}/mensagens/", corpo, format="json", HTTP_IDEMPOTENCY_KEY="k-1"), 201)
        m2 = self.ok(self.api_g.post(f"{V1}/mensagens/", corpo, format="json", HTTP_IDEMPOTENCY_KEY="k-1"), 200)
        self.assertEqual((m1["id"], m2["id"], m2["criada"]), (m1["id"], m1["id"], False))
        self.assertEqual(Mensagem.objects.count(), 1)
        self.assertEqual(len(self.ok(self.api_g.get(f"{V1}/mensagens/", {"conexao": c["id"], "direcao": "saida"}))["itens"]), 1)
        self.assertEqual(self.api_o.get(f"{V1}/integracoes/").status_code, 403)
        self.assertEqual(self.api_o.post(f"{V1}/mensagens/", corpo, format="json").status_code, 403)
        self.assertEqual(self.api_b.post(f"{V1}/mensagens/", corpo, format="json").status_code, 400)  # conexão de outra unidade
        self.assertEqual(self.api_b.get(f"{V1}/integracoes/{c['id']}/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/mensagens/").json()["data"]["itens"], [])
        self.assertEqual(self.api_g.post(f"{V1}/mensagens/", {**corpo, "texto": ""}, format="json").status_code, 422)

    def test_testar_credenciais_sincronizar_e_desconectar(self):
        c = self.ok(self.api_g.post(f"{V1}/integracoes/", {"provedor": "teste", "nome": "Ag", "credenciais": {"segredo": "s"}}, format="json"), 201)
        self.assertTrue(self.ok(self.api_g.post(f"{V1}/integracoes/{c['id']}/testar/"))["ok"])
        self.ok(self.api_g.patch(f"{V1}/integracoes/{c['id']}/credenciais/", {"credenciais": {"segredo": "novo"}}, format="json"))
        self.assertIn("criados", self.ok(self.api_g.post(f"{V1}/integracoes/{c['id']}/sincronizar/")))
        self.assertEqual(self.api_o.post(f"{V1}/integracoes/{c['id']}/desconectar/").status_code, 403)
        self.assertEqual(self.api_b.post(f"{V1}/integracoes/{c['id']}/desconectar/").status_code, 404)
        self.assertEqual(self.ok(self.api_g.post(f"{V1}/integracoes/{c['id']}/desconectar/"))["status"], "revogada")

    def test_compromissos_crud_e_tenant(self):
        inicio = (timezone.now() + timedelta(days=1)).isoformat()
        fim = (timezone.now() + timedelta(days=1, hours=2)).isoformat()
        c = self.ok(self.api_o.post(f"{V1}/compromissos/", {"titulo": "Entrega X", "tipo": "entrega", "inicio": inicio, "fim": fim}, format="json"), 201)
        self.assertEqual((c["tipo"], c["origem"], c["ativo"]), ("entrega", "local", True))
        self.assertEqual(self.api_b.get(f"{V1}/compromissos/{c['id']}/").status_code, 404)
        self.assertEqual(self.api_o.post(f"{V1}/compromissos/", {"titulo": "Ruim", "inicio": fim, "fim": inicio}, format="json").status_code, 400)  # fim antes do início
        self.assertEqual(self.ok(self.api_o.patch(f"{V1}/compromissos/{c['id']}/", {"titulo": "Entrega Y"}, format="json"))["titulo"], "Entrega Y")


class ComandosTests(Base):
    def saida(self, *args):
        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_enviar_mensagens_e_sincronizar_e_renovar_e_rotacionar(self):
        c = self.conexao(config={"renovavel": "1"})
        s.enfileirar_mensagem(self.req, c, "11999990000", "oi")
        self.assertIn("enviadas=1", self.saida("enviar_mensagens_pendentes"))
        self.assertIn("1 conexão(ões) de agenda", self.saida("sincronizar_integracoes"))
        Conexao.objects.filter(pk=c.pk).update(expira_em=timezone.now() + timedelta(minutes=10))
        self.assertIn("1 credencial(is) renovada(s)", self.saida("renovar_credenciais"))
        antes = Conexao.objects.get(pk=c.pk).credenciais_cifradas
        nova = Fernet.generate_key().decode()
        with override_settings(INTEGRACAO_CRYPTO_KEYS=f"{nova},{CHAVE}"):
            self.assertIn("1 conexão(ões) recifrada(s)", self.saida("rotacionar_credenciais"))
            depois = Conexao.objects.get(pk=c.pk).credenciais_cifradas
            self.assertNotEqual(antes, depois)
            self.assertTrue(s.credenciais_de(Conexao.objects.get(pk=c.pk))["token"].startswith("novo-"))
        with override_settings(INTEGRACAO_CRYPTO_KEYS=nova):  # a chave antiga pode ser removida depois da rotação
            self.assertTrue(s.credenciais_de(Conexao.objects.get(pk=c.pk)))
