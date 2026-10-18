"""Google Tarefas (no lugar do Google Keep): sincronização de tarefas da agenda nos dois sentidos, com a API simulada em memória.
NENHUM teste fala com o Google real."""
import json
import urllib.parse
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from cryptography.fernet import Fernet

from apps.cliente.models import Cliente
from . import rede, services as s
from .models import Compromisso, ItemSincronizado
from .tests import Req, usuario

CHAVE = Fernet.generate_key().decode()


class GoogleTarefasFalso:
    """Mini backend da Tasks API: guarda as tarefas e responde como o `rede.requisitar`."""

    def __init__(self):
        self.tarefas, self.chamadas, self.seq = {}, [], 0

    def __call__(self, metodo, url, cabecalhos=None, corpo=None, json_resposta=True):
        self.chamadas.append((metodo, url, corpo))
        partes = urllib.parse.urlsplit(url)
        assert partes.netloc == "tasks.googleapis.com", url
        caminho = partes.path.split("/tasks")[-1]
        if metodo == "GET":
            return 200, {}, {"items": list(self.tarefas.values())}
        if metodo == "POST":
            self.seq += 1
            self.tarefas[f"t{self.seq}"] = {**corpo, "id": f"t{self.seq}", "etag": "e1", "updated": timezone.now().isoformat()}
            return 200, {}, self.tarefas[f"t{self.seq}"]
        tid = caminho.strip("/")
        if metodo == "PATCH":
            self.tarefas[tid].update({k: v for k, v in corpo.items() if v is not None}, updated=timezone.now().isoformat())
            if corpo.get("completed", 1) is None:
                self.tarefas[tid].pop("completed", None)
            return 200, {}, self.tarefas[tid]
        if metodo == "DELETE":
            self.tarefas.pop(tid, None)
            return 204, {}, b""
        raise AssertionError(metodo)

    def mutacoes(self):
        return [c for c in self.chamadas if c[0] != "GET"]


@override_settings(INTEGRACAO_CRYPTO_KEYS=CHAVE)
class TarefasTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        self.gestor = usuario("gestor", self.a, "Gestor")
        self.fake = GoogleTarefasFalso()
        p = mock.patch.object(rede, "requisitar", side_effect=self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.c = s.conectar(Req(self.gestor), self.a, "google_tasks", "Google Tarefas", {"access_token": "a", "refresh_token": "r"}, {"tasklist": "@default"}, testar=False)
        self.amanha = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def tarefa(self, titulo="Ligar", tipo="tarefa", cliente=None, **extra):
        return Compromisso.objects.create(cliente=cliente or self.a, titulo=titulo, tipo=tipo, inicio=self.amanha, fim=self.amanha + timedelta(hours=1), **extra)

    def test_so_tarefas_sao_publicadas_com_data_e_status(self):
        self.tarefa("Ligar")
        self.tarefa("Reunião", tipo="compromisso")
        r = s.sincronizar_agenda(self.c)
        self.assertEqual((r["criados"], r["erros"]), (1, 0))
        tarefa = next(iter(self.fake.tarefas.values()))
        self.assertEqual((tarefa["title"], tarefa["status"]), ("Ligar", "needsAction"))
        self.assertTrue(tarefa["due"].endswith("T00:00:00.000Z"))
        self.assertEqual(len(self.fake.tarefas), 1)  # compromisso comum não vira tarefa

    def test_sincronizacao_repetida_nao_reenvia(self):
        self.tarefa()
        s.sincronizar_agenda(self.c)
        antes = len(self.fake.mutacoes())
        r = s.sincronizar_agenda(self.c)
        self.assertEqual(len(self.fake.mutacoes()), antes)
        self.assertEqual((r["criados"], r["atualizados"], r["puxados"], r["importados"]), (0, 0, 0, 0))

    def test_concluir_localmente_marca_concluida_no_google(self):
        t = self.tarefa()
        s.sincronizar_agenda(self.c)
        Compromisso.objects.filter(pk=t.pk).update(concluido=True)
        s.sincronizar_agenda(self.c)
        self.assertEqual(next(iter(self.fake.tarefas.values()))["status"], "completed")
        Compromisso.objects.filter(pk=t.pk).update(concluido=False)
        s.sincronizar_agenda(self.c)
        self.assertEqual(next(iter(self.fake.tarefas.values()))["status"], "needsAction")

    def test_tarefa_criada_no_google_vira_tarefa_local_da_unidade_e_estabiliza(self):
        dia = (self.amanha + timedelta(days=1)).date().isoformat()
        self.fake.tarefas["x1"] = {"id": "x1", "title": "Comprar caixas", "notes": "urgente", "due": f"{dia}T00:00:00.000Z", "status": "completed", "etag": "e", "updated": timezone.now().isoformat()}
        self.fake.tarefas["sem_data"] = {"id": "sem_data", "title": "Sem data", "status": "needsAction"}  # ignorada
        r = s.sincronizar_agenda(self.c)
        self.assertEqual(r["importados"], 1)
        c = Compromisso.objects.get(titulo="Comprar caixas")
        self.assertEqual((c.cliente, c.tipo, c.concluido, c.origem, c.todo_o_dia), (self.a, "tarefa", True, "externa", True))
        antes = len(self.fake.mutacoes())
        r = s.sincronizar_agenda(self.c)
        self.assertEqual(len(self.fake.mutacoes()), antes)  # sem ping-pong
        self.assertEqual((r["criados"], r["atualizados"], r["importados"]), (0, 0, 0))
        self.assertFalse(Compromisso.objects.filter(cliente=self.b).exists())

    def test_conclusao_mais_recente_no_google_vence(self):
        t = self.tarefa()
        s.sincronizar_agenda(self.c)
        Compromisso.objects.filter(pk=t.pk).update(atualizado_em=timezone.now() - timedelta(hours=1))
        tarefa = next(iter(self.fake.tarefas.values()))
        tarefa.update(status="completed", updated=timezone.now().isoformat())
        s.sincronizar_agenda(self.c)
        t.refresh_from_db()
        self.assertTrue(t.concluido)

    def test_tarefa_excluida_localmente_sai_do_google(self):
        t = self.tarefa()
        s.sincronizar_agenda(self.c)
        Compromisso.objects.filter(pk=t.pk).update(ativo=False)
        s.sincronizar_agenda(self.c)
        self.assertEqual(self.fake.tarefas, {})
        self.assertFalse(ItemSincronizado.objects.exists())

    def test_oauth_inicia_com_escopo_de_tarefas_e_recusa_provedor_desconhecido(self):
        self.client.force_login(self.gestor)
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="seg"):
            r = self.client.post(reverse("integracao-google-iniciar"), {"provedor": "google_tasks"})
            self.assertEqual(r.status_code, 302)
            self.assertIn(urllib.parse.quote("https://www.googleapis.com/auth/tasks", safe=""), r["Location"])
            self.assertEqual(self.client.post(reverse("integracao-google-iniciar"), {"provedor": "whatsapp_cloud"}).status_code, 404)

    def test_calendario_continua_publicando_contas_e_nao_envia_status(self):
        c = s.conectar(Req(self.gestor), self.a, "google_calendar", "Agenda", {"access_token": "a", "refresh_token": "r"}, {"calendar_id": "primary"}, testar=False)
        locais = s.eventos_locais(c)
        self.assertTrue(all("concluido" not in e for e in locais.values()))
