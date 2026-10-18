"""Assinatura: máquina de estados, webhooks (duplicado/inválido/fora de ordem), bloqueio central, tenant e permissão.
O Pagar.me é SEMPRE simulado (FakePagarme): nenhum teste chama o serviço real."""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from web_project.acesso import modulos_habilitados
from web_project.testing import habilitar_modulo

from . import pagarme, services
from .models import Assinatura, Cobranca, EventoWebhook, Plano

TOKEN = "t" * 40


class FakePagarme:
    """Dublê da API: registra chamadas e devolve respostas no formato documentado."""

    def __init__(self):
        self.chamadas, self.cobranca_status, self.falhar = [], "paid", False

    def criar_plano(self, plano):
        self.chamadas.append(("criar_plano", plano.slug))
        return {"id": f"plan_{plano.slug}"}

    def criar_assinatura(self, plano_id, cliente, forma, card_token=None, codigo=""):
        self.chamadas.append(("criar_assinatura", plano_id, forma, card_token))
        return {"id": "sub_123", "status": "active", "next_billing_at": "2030-01-10T00:00:00Z"}

    def obter_assinatura(self, sid):
        if self.falhar:
            raise pagarme.ErroPagarme("fora do ar")
        return {"id": sid, "status": "active", "next_billing_at": "2030-02-10T00:00:00Z"}

    def cancelar_assinatura(self, sid):
        self.chamadas.append(("cancelar", sid))
        return {"status": "canceled"}

    def obter_cobranca(self, cid):
        if self.falhar:
            raise pagarme.ErroPagarme("fora do ar")
        return {"id": cid, "status": self.cobranca_status}


def evento(hook, tipo, sub="sub_123", invoice="in_1", charge="ch_1", **dados):
    return {"id": hook, "type": tipo, "data": {"id": invoice, "amount": 5990, "subscription": {"id": sub}, "charge": {"id": charge}, "payment_method": "boleto", **dados}}


@override_settings(PAGARME_SECRET_KEY="sk_test_x", PAGARME_WEBHOOK_TOKEN=TOKEN, PAGARME_CONFIRMAR_PAGAMENTO=True, ASSINATURA_CARENCIA_DIAS=7)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        self.filial = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000262", nome_razao_social="Filial A", tipo_estabelecimento="filial", matriz=self.a)
        for c in (self.a, self.b, self.filial):
            habilitar_modulo(c, "insumo")
        self.gestor_a = self.usuario("gestor_a", self.a, "Gestor")
        self.gestor_b = self.usuario("gestor_b", self.b, "Gestor")
        self.operador_a = self.usuario("operador_a", self.a, "Operador")
        self.fake = FakePagarme()
        patcher = mock.patch("apps.assinatura.pagarme.obter_cliente", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def usuario(self, nome, cliente, grupo):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
        u.groups.add(Group.objects.get(name=grupo))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def assinatura(self, cliente=None, status=Assinatura.PENDENTE, plano="mensal", sub="sub_123", **extra):
        return Assinatura.objects.create(cliente=cliente or self.a, plano=Plano.objects.get(slug=plano), status=status, pagarme_subscription_id=sub, **extra)

    def webhook(self, payload, token=TOKEN, cru=None):
        return self.client.post(reverse("assinatura-webhook", args=[token]), data=cru if cru is not None else json.dumps(payload), content_type="application/json")


class PlanosTests(Base):
    def test_planos_iniciais_em_centavos(self):
        self.assertEqual(Plano.objects.get(slug="mensal").valor, 5990)
        self.assertEqual(Plano.objects.get(slug="anual").valor, 42900)
        self.assertEqual(Plano.objects.get(slug="anual").meses, 12)


class WebhookTests(Base):
    def test_pagamento_ativa_e_e_idempotente(self):
        a = self.assinatura()
        self.assertEqual(self.webhook(evento("hook_1", "invoice.paid")).content, b"pagamento_registrado")
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.ATIVA)
        vigencia = a.vigente_ate
        self.assertIsNotNone(vigencia)
        self.assertEqual(self.webhook(evento("hook_1", "invoice.paid")).content, b"duplicado")  # mesmo hook: sem efeito
        self.assertEqual(self.webhook(evento("hook_2", "invoice.paid")).content, b"ja_paga")  # mesma fatura, outro hook
        a.refresh_from_db()
        self.assertEqual(a.vigente_ate, vigencia)
        self.assertEqual(Cobranca.objects.get().status, Cobranca.PAGA)

    def test_fatura_nova_renova_a_vigencia(self):
        a = self.assinatura()
        self.webhook(evento("h1", "invoice.paid", invoice="in_1"))
        a.refresh_from_db()
        primeira = a.vigente_ate
        self.webhook(evento("h2", "invoice.paid", invoice="in_2", charge="ch_2"))
        a.refresh_from_db()
        self.assertGreater(a.vigente_ate, primeira + timedelta(days=27))

    def test_token_errado_ou_ausente_404_e_nada_gravado(self):
        self.assinatura()
        self.assertEqual(self.webhook(evento("h", "invoice.paid"), token="errado").status_code, 404)
        with override_settings(PAGARME_WEBHOOK_TOKEN=""):  # sem segredo configurado a rota inteira "não existe"
            self.assertEqual(self.webhook(evento("h", "invoice.paid"), token="qualquer").status_code, 404)
        self.assertFalse(EventoWebhook.objects.exists())

    def test_payload_invalido_400_e_corpo_grande_413(self):
        self.assertEqual(self.webhook(None, cru="não é json").status_code, 400)
        self.assertEqual(self.webhook({"foo": 1}).status_code, 400)
        self.assertEqual(self.webhook(None, cru="x" * (70 * 1024)).status_code, 413)

    def test_get_nao_permitido(self):
        self.assertEqual(self.client.get(reverse("assinatura-webhook", args=[TOKEN])).status_code, 405)

    def test_pagamento_nao_confirmado_pelo_provedor_nao_ativa(self):
        a = self.assinatura()
        self.fake.cobranca_status = "pending"
        self.assertEqual(self.webhook(evento("h", "invoice.paid")).content, b"nao_confirmado_pelo_provedor")
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.PENDENTE)

    def test_provedor_fora_do_ar_devolve_503_e_permite_reenvio(self):
        a = self.assinatura()
        self.fake.falhar = True
        self.assertEqual(self.webhook(evento("h", "invoice.paid")).status_code, 503)
        self.assertFalse(EventoWebhook.objects.exists())  # nada ficou marcado como processado
        self.fake.falhar = False
        self.assertEqual(self.webhook(evento("h", "invoice.paid")).content, b"pagamento_registrado")
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.ATIVA)

    def test_falha_depois_do_pagamento_nao_rebaixa(self):  # evento fora de ordem
        a = self.assinatura()
        self.webhook(evento("h1", "invoice.paid"))
        self.assertEqual(self.webhook(evento("h2", "invoice.payment_failed")).content, b"ignorado_fora_de_ordem")
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.ATIVA)

    def test_falha_entra_em_atraso_e_pagamento_regulariza(self):
        a = self.assinatura(status=Assinatura.ATIVA)
        self.webhook(evento("h1", "invoice.payment_failed", invoice="in_9"))
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.EM_ATRASO)
        self.webhook(evento("h2", "invoice.paid", invoice="in_9"))
        a.refresh_from_db()
        self.assertEqual((a.status, a.em_atraso_desde), (Assinatura.ATIVA, None))

    def test_assinatura_desconhecida_e_ignorada_sem_erro(self):
        self.assertEqual(self.webhook(evento("h", "invoice.paid", sub="sub_outra")).content, b"assinatura_desconhecida")

    def test_cancelamento_pelo_provedor(self):
        a = self.assinatura(status=Assinatura.ATIVA)
        self.webhook({"id": "h", "type": "subscription.canceled", "data": {"id": "sub_123"}})
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.CANCELADA)

    def test_fatura_de_outro_tenant_nao_cruza(self):
        a, b = self.assinatura(), self.assinatura(cliente=self.b, sub="sub_b")
        self.webhook(evento("h1", "invoice.paid", sub="sub_b", invoice="in_b", charge="ch_b"))
        self.webhook(evento("h2", "invoice.paid", sub="sub_123", invoice="in_b", charge="ch_b"))  # mesma fatura citando a assinatura errada
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.status, b.status), (Assinatura.PENDENTE, Assinatura.ATIVA))


class VerificacaoTests(Base):
    def test_carencia_depois_bloqueio_e_regularizacao_automatica(self):
        agora = timezone.now()
        a = self.assinatura(status=Assinatura.EM_ATRASO, em_atraso_desde=agora - timedelta(days=3))
        self.assertEqual(services.verificar_assinaturas(agora)["bloqueadas"], 0)  # ainda na carência
        self.assertFalse(services.acesso_bloqueado(self.a))
        self.assertEqual(services.verificar_assinaturas(agora + timedelta(days=5))["bloqueadas"], 1)
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.BLOQUEADA)
        self.assertTrue(services.acesso_bloqueado(self.a))
        self.assertEqual(services.verificar_assinaturas(agora + timedelta(days=6))["bloqueadas"], 0)  # idempotente
        self.webhook(evento("h", "invoice.paid"))  # paga => desbloqueia sozinho
        a.refresh_from_db()
        self.assertEqual(a.status, Assinatura.ATIVA)
        self.assertFalse(services.acesso_bloqueado(self.a))

    def test_mensal_vencida_vira_atraso_e_anual_vencida_expira(self):
        agora = timezone.now()
        m = self.assinatura(status=Assinatura.ATIVA, vigente_ate=agora - timedelta(days=1))
        an = self.assinatura(cliente=self.b, status=Assinatura.ATIVA, plano="anual", sub="sub_b", vigente_ate=agora - timedelta(days=1))
        resumo = services.verificar_assinaturas(agora)
        m.refresh_from_db()
        an.refresh_from_db()
        self.assertEqual((m.status, an.status), (Assinatura.EM_ATRASO, Assinatura.EXPIRADA))
        self.assertEqual((resumo["em_atraso"], resumo["expiradas"]), (1, 1))

    def test_aviso_de_renovacao_uma_vez(self):
        agora = timezone.now()
        self.assinatura(status=Assinatura.ATIVA, vigente_ate=agora + timedelta(days=10))
        self.assertEqual(services.verificar_assinaturas(agora)["avisos"], 1)
        self.assertEqual(services.verificar_assinaturas(agora)["avisos"], 0)

    def test_reconcilia_e_tolera_provedor_fora_do_ar(self):
        a = self.assinatura(status=Assinatura.ATIVA, vigente_ate=timezone.now() + timedelta(days=90))
        self.assertEqual(services.verificar_assinaturas()["reconciliadas"], 1)
        a.refresh_from_db()
        self.assertEqual(a.proxima_cobranca_em.year, 2030)
        self.fake.falhar = True
        self.assertEqual(services.verificar_assinaturas()["erros"], 1)

    def test_comando(self):
        call_command("verificar_assinaturas", "--sem-provedor", verbosity=0)


class BloqueioTests(Base):
    def test_bloqueio_central_limita_modulos_e_nao_afeta_outro_tenant_nem_admin(self):
        self.assinatura(status=Assinatura.BLOQUEADA)
        self.assertEqual(modulos_habilitados(self.gestor_a) - {"painel", "assinatura", "perfil-foto"}, set())
        self.assertIn("insumo", modulos_habilitados(self.gestor_b))
        # filial é coberta pela assinatura da matriz
        self.assertTrue(services.acesso_bloqueado(self.filial))
        admin = self.usuario("adm", self.a, "Administrador")
        self.assertIn("insumo", modulos_habilitados(admin))
        self.client.force_login(self.gestor_a)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)
        self.assertEqual(self.client.get(reverse("assinatura")).status_code, 200)  # tela de regularização continua

    def test_cancelada_mantem_acesso_ate_o_fim_do_periodo(self):
        a = self.assinatura(status=Assinatura.CANCELADA, vigente_ate=timezone.now() + timedelta(days=5))
        self.assertFalse(services.acesso_bloqueado(self.a))
        a.vigente_ate = timezone.now() - timedelta(days=1)
        a.save()
        self.assertTrue(services.acesso_bloqueado(self.a))

    def test_sem_assinatura_nao_bloqueia_salvo_se_obrigatoria(self):
        self.assertFalse(services.acesso_bloqueado(self.a))
        with override_settings(ASSINATURA_OBRIGATORIA=True):
            self.assertTrue(services.acesso_bloqueado(self.a))


class AcoesTests(Base):
    def test_assinar_boleto_cria_pendente_audita_e_sincroniza_plano(self):
        self.client.force_login(self.gestor_a)
        self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "boleto"})
        a = Assinatura.objects.get()
        self.assertEqual((a.cliente, a.status, a.pagarme_subscription_id), (self.a, Assinatura.PENDENTE, "sub_123"))
        self.assertEqual(Plano.objects.get(slug="mensal").pagarme_plan_id, "plan_mensal")
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="assinatura", acao="criar").exists())

    def test_cartao_exige_token_e_nunca_recebe_numero(self):
        self.client.force_login(self.gestor_a)
        self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "credit_card", "numero": "4111111111111111"})
        self.assertFalse(Assinatura.objects.exists())
        self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "credit_card", "card_token": "token_abc"})
        self.assertEqual(self.fake.chamadas[-1], ("criar_assinatura", "plan_mensal", "credit_card", "token_abc"))

    def test_nao_assina_duas_vezes_e_cancela(self):
        self.client.force_login(self.gestor_a)
        self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "boleto"})
        self.client.post(reverse("assinatura-assinar"), {"plano": "anual", "forma": "boleto"})
        self.assertEqual(Assinatura.objects.count(), 1)
        self.client.post(reverse("assinatura-cancelar"))
        self.assertEqual(Assinatura.objects.get().status, Assinatura.CANCELADA)
        self.assertIn(("cancelar", "sub_123"), self.fake.chamadas)

    def test_operador_sem_permissao(self):
        self.client.force_login(self.operador_a)
        self.assertEqual(self.client.get(reverse("assinatura")).status_code, 403)
        self.assertEqual(self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "boleto"}).status_code, 403)

    def test_sem_credenciais_fica_indisponivel_com_mensagem(self):
        self.client.force_login(self.gestor_a)
        with mock.patch("apps.assinatura.pagarme.obter_cliente", side_effect=pagarme.NaoConfigurado("Integração com o Pagar.me não configurada.")):
            r = self.client.post(reverse("assinatura-assinar"), {"plano": "mensal", "forma": "boleto"}, follow=True)
        self.assertContains(r, "não configurada")
        self.assertFalse(Assinatura.objects.exists())


class AbaDoClienteTests(Base):
    def test_aba_aparece_para_quem_edita_o_cliente_e_tem_permissao(self):
        self.assinatura()
        self.client.force_login(self.usuario("adm", self.a, "Administrador"))
        self.assertContains(self.client.get(reverse("cliente-edit", args=[self.a.pk])), "Pagamentos / Assinatura")

    def test_aba_oculta_sem_permissao_de_assinatura(self):
        self.assinatura()
        sem = self.usuario("adm2", self.a, "Administrador")
        sem.groups.clear()
        sem.is_superuser = False
        sem.save()
        self.client.force_login(sem)
        self.assertNotContains(self.client.get(reverse("cliente-edit", args=[self.a.pk])), "Pagamentos / Assinatura", status_code=403)

    def test_painel_service_isolamento(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            services.painel_do_cliente(self.gestor_a, self.b.pk)
        self.assertEqual(services.painel_do_cliente(self.gestor_a, self.filial.pk)["cliente"], self.filial)
