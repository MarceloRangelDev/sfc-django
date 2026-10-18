"""Agenda: tipos evento/tarefa, concluir/excluir, visão calendário (JSON) e arrastar e soltar, sempre dentro do tenant."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from web_project.testing import habilitar_modulo

from .models import Compromisso


class AgendaTiposECalendarioTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        for c in (self.a, self.b):
            c.perfis.set([Group.objects.get(name="Gestor")])
            habilitar_modulo(c, "agenda")
        self.gestor = self._usuario("gestor_a", self.a)
        self.gestor_b = self._usuario("gestor_b", self.b)
        self.client.force_login(self.gestor)
        self.inicio = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def _usuario(self, nome, cliente):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
        u.groups.add(Group.objects.get(name="Gestor"))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def criar(self, titulo="Pagar fornecedor", tipo="tarefa", cliente=None):
        return Compromisso.objects.create(cliente=cliente or self.a, titulo=titulo, tipo=tipo, inicio=self.inicio, fim=self.inicio + timedelta(hours=1))

    def test_tipos_evento_e_tarefa_no_formulario(self):
        dados = {"tipo": "tarefa", "titulo": "Ligar", "descricao": "", "inicio": self.inicio.strftime("%Y-%m-%dT%H:%M"), "fim": (self.inicio + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")}
        self.assertEqual(self.client.post(reverse("compromisso-create"), dados).status_code, 302)
        self.client.post(reverse("compromisso-create"), {**dados, "tipo": "evento", "titulo": "Feira"})
        self.assertEqual(set(Compromisso.objects.values_list("tipo", flat=True)), {"tarefa", "evento"})

    def test_concluir_alterna_e_audita(self):
        c = self.criar()
        self.client.post(reverse("compromisso-concluir", args=[c.pk]))
        c.refresh_from_db()
        self.assertTrue(c.concluido)
        self.client.post(reverse("compromisso-concluir", args=[c.pk]))
        c.refresh_from_db()
        self.assertFalse(c.concluido)
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="integracao", acao="editar", registro_id=str(c.pk)).count(), 2)

    def test_excluir_e_exclusao_logica_e_auditada(self):
        c = self.criar()
        self.client.post(reverse("compromisso-excluir", args=[c.pk]))
        c.refresh_from_db()
        self.assertFalse(c.ativo)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="integracao", acao="excluir", registro_id=str(c.pk)).exists())
        self.assertNotContains(self.client.get(reverse("compromisso-list")), "Pagar fornecedor")

    def test_outro_tenant_nao_conclui_nao_exclui_nem_move(self):
        c = self.criar()
        self.client.force_login(self.gestor_b)
        for nome in ("compromisso-concluir", "compromisso-excluir"):
            self.assertEqual(self.client.post(reverse(nome, args=[c.pk])).status_code, 404, nome)
        self.assertEqual(self.client.post(reverse("compromisso-mover", args=[c.pk]), {"inicio": self.inicio.isoformat()}, content_type="application/json").status_code, 404)
        c.refresh_from_db()
        self.assertEqual((c.ativo, c.concluido, c.inicio), (True, False, self.inicio))

    def test_eventos_json_so_do_escopo_e_marca_concluidos(self):
        c = self.criar()
        self.criar("De B", cliente=self.b)
        self.client.post(reverse("compromisso-concluir", args=[c.pk]))
        inicio, fim = (self.inicio - timedelta(days=1)).isoformat(), (self.inicio + timedelta(days=2)).isoformat()
        eventos = self.client.get(reverse("compromisso-eventos"), {"start": inicio, "end": fim}).json()
        self.assertEqual([e["title"] for e in eventos], ["Pagar fornecedor"])
        self.assertIn("concluido", eventos[0]["classNames"])
        self.assertIn("tipo-tarefa", eventos[0]["classNames"])

    def test_arrastar_para_outro_dia_mantem_a_duracao(self):
        c = self.criar()
        novo = self.inicio + timedelta(days=3)
        r = self.client.post(reverse("compromisso-mover", args=[c.pk]), {"inicio": novo.isoformat()}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        c.refresh_from_db()
        self.assertEqual((c.inicio, c.fim), (novo, novo + timedelta(hours=1)))
        self.assertEqual(self.client.post(reverse("compromisso-mover", args=[c.pk]), {"inicio": "lixo"}, content_type="application/json").status_code, 400)
        fim_antes = (novo - timedelta(hours=1)).isoformat()
        self.assertEqual(self.client.post(reverse("compromisso-mover", args=[c.pk]), {"inicio": novo.isoformat(), "fim": fim_antes}, content_type="application/json").status_code, 400)

    def test_visoes_tabela_e_calendario(self):
        self.criar()
        self.assertNotContains(self.client.get(reverse("compromisso-list")), 'id="agendaCalendario"')  # tabela é o padrão
        pagina = self.client.get(reverse("compromisso-list"), {"visao": "calendario"})
        self.assertContains(pagina, 'id="agendaCalendario"')
        self.assertContains(pagina, "fullcalendar.js")
        self.assertContains(self.client.get(reverse("compromisso-list"), {"visao": "tabela"}), "Pagar fornecedor")
