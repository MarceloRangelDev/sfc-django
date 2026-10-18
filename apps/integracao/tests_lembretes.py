"""Agenda: lembretes (idempotência, escopo de unidade), resumo do dia e salvar pelo modal do calendário."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.notificacao.models import DestinatarioNotificacao
from web_project.testing import habilitar_modulo

from .lembretes import enviar_lembretes, resumo_do_dia
from .models import Compromisso


class Base(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        for c in (self.a, self.b):
            habilitar_modulo(c, "agenda")
        self.ua = self.usuario("ua", self.a)
        self.ub = self.usuario("ub", self.b)
        self.agora = timezone.now().replace(microsecond=0)

    def usuario(self, nome, cliente):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
        u.groups.add(Group.objects.get(name="Gestor"))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def comp(self, minutos_ate_inicio, lembrete, cliente=None, **extra):
        inicio = self.agora + timedelta(minutes=minutos_ate_inicio)
        return Compromisso.objects.create(cliente=cliente or self.a, titulo="Reunião", inicio=inicio, fim=inicio + timedelta(hours=1), lembrete_minutos=lembrete, **extra)


class LembreteTests(Base):
    def test_envia_so_quando_vence_e_so_uma_vez(self):
        c = self.comp(30, 15)  # lembrete 15 min antes, falta 30: ainda não
        self.assertEqual(enviar_lembretes(self.agora), 0)
        self.assertEqual(enviar_lembretes(self.agora + timedelta(minutes=16)), 1)
        self.assertEqual(enviar_lembretes(self.agora + timedelta(minutes=17)), 0)  # idempotente
        c.refresh_from_db()
        self.assertIsNotNone(c.lembrete_enviado_em)
        self.assertEqual(DestinatarioNotificacao.objects.filter(usuario=self.ua).count(), 1)

    def test_escopo_so_a_unidade_do_compromisso(self):
        self.comp(0, 0)
        enviar_lembretes(self.agora)
        self.assertEqual(DestinatarioNotificacao.objects.filter(usuario=self.ub).count(), 0)

    def test_nao_envia_concluido_inativo_sem_lembrete_ou_muito_atrasado(self):
        self.comp(0, 0, concluido=True)
        self.comp(0, 0, ativo=False)
        self.comp(0, None)
        self.comp(-60 * 30, 0)  # começou há 30 h: fora da janela
        self.assertEqual(enviar_lembretes(self.agora), 0)

    def test_reagendar_reabilita_o_lembrete(self):
        c = self.comp(0, 0)
        enviar_lembretes(self.agora)
        self.client.force_login(self.ua)
        nova = self.agora + timedelta(days=1)
        r = self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), {
            "tipo": "compromisso", "titulo": "Reunião", "inicio": timezone.localtime(nova).strftime("%Y-%m-%dT%H:%M"),
            "fim": timezone.localtime(nova + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"), "lembrete_minutos": "0", "ativo": "on",
        })
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertIsNone(c.lembrete_enviado_em)

    def test_comando(self):
        self.comp(0, 0)
        call_command("enviar_lembretes_agenda", verbosity=0)
        self.assertEqual(DestinatarioNotificacao.objects.filter(usuario=self.ua).count(), 1)


class ResumoTests(Base):
    def test_resumo_uma_vez_por_dia_e_so_da_propria_unidade(self):
        self.comp(60, None)
        self.comp(60, None, cliente=self.b)
        self.assertIsNotNone(resumo_do_dia(self.ua, self.agora))
        self.assertIsNone(resumo_do_dia(self.ua, self.agora))  # já avisado hoje
        self.assertIn("1 para hoje", DestinatarioNotificacao.objects.get(usuario=self.ua).notificacao.mensagem)

    def test_sem_nada_a_mostrar_nao_notifica(self):
        self.assertIsNone(resumo_do_dia(self.ua, self.agora))

    def test_login_dispara_resumo_e_falha_nao_quebra_login(self):
        self.comp(60, None)
        self.client.force_login(self.ua)  # garante o sinal user_logged_in
        self.assertTrue(DestinatarioNotificacao.objects.filter(usuario=self.ua).exists())
        with mock.patch("apps.integracao.lembretes.resumo_do_dia", side_effect=RuntimeError("falha")):
            self.client.force_login(self.ub)  # não levanta


class ModalSalvarTests(Base):
    dados = lambda self: {  # noqa: E731
        "tipo": "tarefa", "titulo": "Ligar", "descricao": "", "inicio": "2030-01-10T09:00", "fim": "2030-01-10T10:00", "lembrete_minutos": "15", "ativo": "on",
    }

    def test_cria_na_unidade_do_usuario_e_audita(self):
        self.client.force_login(self.ua)
        r = self.client.post(reverse("compromisso-salvar"), self.dados())
        self.assertEqual(r.status_code, 200)
        c = Compromisso.objects.get()
        self.assertEqual((c.cliente, c.lembrete_minutos), (self.a, 15))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="integracao", acao="criar").exists())

    def test_erro_de_validacao_volta_400_com_mensagens(self):
        self.client.force_login(self.ua)
        r = self.client.post(reverse("compromisso-salvar"), {**self.dados(), "fim": "2030-01-10T08:00"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("fim", r.json()["erros"])
        self.assertFalse(Compromisso.objects.exists())

    def test_outra_unidade_nao_edita(self):
        c = self.comp(60, None)
        self.client.force_login(self.ub)
        self.assertEqual(self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), self.dados()).status_code, 404)

    def test_get_nao_permitido_e_eventos_trazem_dados_do_formulario(self):
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(reverse("compromisso-salvar")).status_code, 405)
        self.comp(60, 60)
        eventos = self.client.get(reverse("compromisso-eventos")).json()
        self.assertEqual(eventos[0]["extendedProps"]["form"]["lembrete_minutos"], "60")
