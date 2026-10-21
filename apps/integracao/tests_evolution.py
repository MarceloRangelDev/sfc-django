"""WhatsApp via Evolution API v2: contrato HTTP, autenticação do webhook por cabeçalho secreto e isolamento. A rede é sempre simulada."""
import json
from unittest import mock

from django.urls import reverse

from . import rede, services as s
from .adaptadores import ErroIntegracao
from .adaptadores.whatsapp import WhatsAppEvolution
from .models import Mensagem
from .tests import Base

CONFIG = {"base_url": "https://evo.exemplo.com", "instancia": "loja-a"}
CRED = {"apikey": "chave-da-instancia", "segredo_webhook": "segredo-do-webhook"}
PUBLICO = [(0, 0, 0, "", ("8.8.8.8", 443))]


def payload(id_="3EB01", instancia="loja-a", de="5511999990000@s.whatsapp.net", from_me=False, texto="Quero 2 bolos"):
    return {"event": "messages.upsert", "instance": instancia, "data": {
        "key": {"remoteJid": de, "fromMe": from_me, "id": id_}, "message": {"conversation": texto}, "messageTimestamp": 1700000000}}


class EvolutionAdaptadorTests(Base):
    def setUp(self):
        super().setUp()
        self.a = WhatsAppEvolution()
        self.c = mock.Mock(config=CONFIG)

    def test_valida_nome_da_instancia(self):
        with self.assertRaises(ErroIntegracao):
            self.a.validar(CRED, {**CONFIG, "instancia": "../admin"})
        with mock.patch("socket.getaddrinfo", return_value=PUBLICO):
            self.a.validar(CRED, CONFIG)

    def test_enviar_usa_apikey_e_contrato_v2(self):
        with mock.patch("socket.getaddrinfo", return_value=PUBLICO), mock.patch.object(rede, "requisitar", return_value=(201, {}, {"key": {"id": "ABC"}})) as req:
            self.assertEqual(self.a.enviar_mensagem(self.c, CRED, "+5511999990000", "oi"), "ABC")
        metodo, url, cabecalhos, corpo = req.call_args[0]
        self.assertEqual((metodo, url), ("POST", "https://evo.exemplo.com/message/sendText/loja-a"))
        self.assertEqual(cabecalhos, {"apikey": "chave-da-instancia"})
        self.assertEqual(corpo, {"number": "5511999990000", "text": "oi"})

    def test_testar_exige_instancia_conectada(self):
        with mock.patch("socket.getaddrinfo", return_value=PUBLICO):
            with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"instance": {"state": "open"}})):
                self.a.testar(self.c, CRED)
            with mock.patch.object(rede, "requisitar", return_value=(200, {}, {"instance": {"state": "close"}})):
                with self.assertRaises(ErroIntegracao):
                    self.a.testar(self.c, CRED)

    def test_assinatura_e_o_cabecalho_secreto(self):
        self.assertTrue(self.a.assinatura_valida(self.c, CRED, {"X-Webhook-Secret": "segredo-do-webhook"}, b""))
        self.assertFalse(self.a.assinatura_valida(self.c, CRED, {"X-Webhook-Secret": "outro"}, b""))
        self.assertFalse(self.a.assinatura_valida(self.c, CRED, {}, b""))
        self.assertFalse(self.a.assinatura_valida(self.c, {"segredo_webhook": ""}, {"X-Webhook-Secret": ""}, b""))

    def test_interpreta_so_mensagens_recebidas_da_instancia(self):
        ok = self.a.interpretar_webhook(self.c, payload())
        self.assertEqual([(e["id"], e["de"], e["texto"]) for e in ok], [("3EB01", "5511999990000", "Quero 2 bolos")])
        self.assertEqual(self.a.interpretar_webhook(self.c, payload(from_me=True)), [])
        self.assertEqual(self.a.interpretar_webhook(self.c, payload(de="1203@g.us")), [])  # grupo
        self.assertEqual(self.a.interpretar_webhook(self.c, payload(instancia="loja-b")), [])  # outra instância
        self.assertEqual(self.a.interpretar_webhook(self.c, {**payload(), "event": "connection.update"}), [])
        self.assertEqual(len(self.a.interpretar_webhook(self.c, {**payload(), "event": "MESSAGES_UPSERT"})), 1)


class EvolutionWebhookTests(Base):
    def setUp(self):
        super().setUp()
        with mock.patch("socket.getaddrinfo", return_value=PUBLICO):
            self.c = s.conectar(self.req, self.cliente, "whatsapp_evolution", "Loja A", CRED, CONFIG, testar=False)
        self.url = reverse("integracao-webhook", args=[self.c.webhook_token])

    def post(self, corpo, segredo="segredo-do-webhook"):
        extra = {"HTTP_X_WEBHOOK_SECRET": segredo} if segredo is not None else {}
        return self.client.post(self.url, json.dumps(corpo), content_type="application/json", **extra)

    def test_recebe_idempotente_e_rejeita_sem_segredo(self):
        self.assertEqual(self.post(payload()).status_code, 200)
        self.assertEqual(self.post(payload()).status_code, 200)
        self.assertEqual(Mensagem.objects.filter(conexao=self.c, direcao="entrada").count(), 1)
        self.assertEqual(self.post(payload("OUTRO"), segredo=None).status_code, 403)
        self.assertEqual(self.post(payload("OUTRO"), segredo="errado").status_code, 403)
        self.assertEqual(Mensagem.objects.count(), 1)

    def test_credenciais_nao_vao_para_o_banco_em_texto_puro(self):
        self.assertNotIn("chave-da-instancia", self.c.credenciais_cifradas)
