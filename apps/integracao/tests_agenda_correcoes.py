"""Agenda: criar/editar pelo modal do calendário sem perder dados, dia inteiro sem deslocar de dia, arrastar e soltar, e a sincronização com o
Google Agenda (paginação, fusos, cancelados, sem duplicar, sem misturar clientes, erros úteis). A rede é sempre simulada em memória."""
import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone as fuso_utc
from unittest import mock

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.cliente.models import Cliente
from web_project.testing import habilitar_modulo

from . import rede, services as s
from .models import Compromisso, Conexao, ItemSincronizado
from .tests import Req, usuario

CHAVE = Fernet.generate_key().decode()
V1 = "/api/v1"


class AgendaBase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        for c in (self.a, self.b):
            habilitar_modulo(c, "agenda")
        self.gestor = usuario("gestor_a", self.a, "Gestor")
        self.operador = usuario("operador_a", self.a, "Operador")
        self.client.force_login(self.gestor)
        self.amanha = (timezone.localtime() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

    def modal(self, **extra):
        """O que o modal do calendário envia ao CRIAR (sem `ativo` nem `concluido`: o modal nem tem esses campos)."""
        inicio = self.amanha.strftime("%Y-%m-%dT%H:%M")
        return {"tipo": "compromisso", "titulo": "Reunião", "descricao": "pauta", "inicio": inicio, "fim": (self.amanha + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "lembrete_minutos": "", "todo_o_dia": "", **extra}

    def eventos(self):
        inicio = (self.amanha - timedelta(days=30)).isoformat()
        fim = (self.amanha + timedelta(days=30)).isoformat()
        return self.client.get(reverse("compromisso-eventos"), {"start": inicio, "end": fim}).json()


class ModalDoCalendarioTests(AgendaBase):
    def test_criar_clicando_na_data_persiste_ativo_e_aparece_no_calendario(self):
        r = self.client.post(reverse("compromisso-salvar"), self.modal())
        self.assertEqual(r.status_code, 200, r.content)
        c = Compromisso.objects.get()
        self.assertEqual((c.cliente_id, c.ativo, c.concluido, c.titulo), (self.a.pk, True, False, "Reunião"))
        self.assertEqual([e["id"] for e in self.eventos()], [c.pk])  # estava sendo criado inativo e por isso sumia

    def test_validacoes_do_modal(self):
        for extra in ({"titulo": ""}, {"inicio": ""}, {"fim": (self.amanha - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")}, {"tipo": "invalido"}):
            r = self.client.post(reverse("compromisso-salvar"), self.modal(**extra))
            self.assertEqual(r.status_code, 400, extra)
            self.assertIn("erros", r.json())
        self.assertFalse(Compromisso.objects.exists())

    def test_editar_altera_so_o_enviado_e_preserva_concluido_ativo_e_omitidos(self):
        c = Compromisso.objects.create(cliente=self.a, titulo="Antigo", descricao="detalhes importantes", inicio=self.amanha, fim=self.amanha + timedelta(hours=1), concluido=True, lembrete_minutos=15)
        # corpo mínimo: só o título (o modal, antes, zerava descrição, lembrete, concluído e ativo)
        r = self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), {"titulo": "Novo título"})
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual((c.titulo, c.descricao, c.concluido, c.ativo, c.lembrete_minutos), ("Novo título", "detalhes importantes", True, True, 15))
        self.assertEqual(c.inicio, self.amanha)

    def test_desmarcar_dia_inteiro_e_decisao_explicita(self):
        c = Compromisso.objects.create(cliente=self.a, titulo="Feriado", inicio=self.amanha, fim=self.amanha, todo_o_dia=True)
        self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), {"titulo": "Feriado"})  # sem a chave: mantém
        c.refresh_from_db()
        self.assertTrue(c.todo_o_dia)
        self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), self.modal(titulo="Feriado"))  # todo_o_dia vazio: desmarcou
        c.refresh_from_db()
        self.assertFalse(c.todo_o_dia)

    def test_outro_cliente_e_operador_sem_permissao(self):
        c = Compromisso.objects.create(cliente=self.b, titulo="Alheio", inicio=self.amanha, fim=self.amanha + timedelta(hours=1))
        self.assertEqual(self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), {"titulo": "X"}).status_code, 404)
        c.refresh_from_db()
        self.assertEqual(c.titulo, "Alheio")
        self.client.force_login(self.operador)  # Operador altera, mas só os da própria unidade
        self.assertEqual(self.client.post(reverse("compromisso-salvar-edit", args=[c.pk]), {"titulo": "X"}).status_code, 404)

    def test_dia_inteiro_e_normalizado_para_as_datas(self):
        inicio, fim = self.amanha.replace(hour=21), (self.amanha + timedelta(days=1)).replace(hour=3)
        self.client.post(reverse("compromisso-salvar"), self.modal(todo_o_dia="on", inicio=inicio.strftime("%Y-%m-%dT%H:%M"), fim=fim.strftime("%Y-%m-%dT%H:%M")))
        c = Compromisso.objects.get()
        self.assertEqual((c.data_inicial, c.data_final), (inicio.date(), fim.date()))
        self.assertEqual((timezone.localtime(c.inicio).hour, timezone.localtime(c.fim).hour), (0, 23))


class DiaInteiroETempoTests(AgendaBase):
    def test_dia_inteiro_importado_aparece_no_dia_certo_com_datas(self):
        dia = (self.amanha + timedelta(days=2)).date()
        c = Compromisso.objects.create(cliente=self.a, titulo="Feriado", origem="externa", todo_o_dia=True,
                                       inicio=datetime(dia.year, dia.month, dia.day, tzinfo=fuso_utc.utc), fim=datetime(dia.year, dia.month, dia.day, 23, 59, tzinfo=fuso_utc.utc))
        e = self.eventos()[0]
        self.assertEqual((e["start"], e["end"], e["allDay"]), (dia.isoformat(), (dia + timedelta(days=1)).isoformat(), True))  # sem "dia anterior" por causa do fuso
        self.assertEqual((e["extendedProps"]["form"]["inicio"], e["extendedProps"]["form"]["fim"]), (f"{dia}T00:00", f"{dia}T23:59"))
        self.assertEqual(c.data_inicial, dia)

    def test_arrastar_dia_inteiro_com_datas_mantem_os_dias(self):
        dia = (self.amanha + timedelta(days=1)).date()
        c = Compromisso.objects.create(cliente=self.a, titulo="Viagem", todo_o_dia=True, inicio=timezone.make_aware(datetime.combine(dia, datetime.min.time())),
                                       fim=timezone.make_aware(datetime.combine(dia + timedelta(days=2), datetime.min.time().replace(hour=23, minute=59))))
        novo = dia + timedelta(days=5)
        r = self.client.post(reverse("compromisso-mover", args=[c.pk]), json.dumps({"inicio": novo.isoformat(), "fim": None, "todo_o_dia": True}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)  # antes: 400 "Data inválida" para dia inteiro
        c.refresh_from_db()
        self.assertEqual((c.data_inicial, c.data_final, c.todo_o_dia), (novo, novo + timedelta(days=2), True))

    def test_arrastar_evento_com_hora_e_de_dia_inteiro_para_horario(self):
        c = Compromisso.objects.create(cliente=self.a, titulo="Reunião", inicio=self.amanha, fim=self.amanha + timedelta(hours=2))
        novo = self.amanha + timedelta(days=3)
        self.client.post(reverse("compromisso-mover", args=[c.pk]), json.dumps({"inicio": novo.isoformat(), "todo_o_dia": False}), content_type="application/json")
        c.refresh_from_db()
        self.assertEqual((c.inicio, c.fim - c.inicio), (novo, timedelta(hours=2)))
        dia = Compromisso.objects.create(cliente=self.a, titulo="Dia", todo_o_dia=True, inicio=self.amanha.replace(hour=0), fim=self.amanha.replace(hour=23, minute=59))
        self.client.post(reverse("compromisso-mover", args=[dia.pk]), json.dumps({"inicio": novo.isoformat(), "todo_o_dia": False}), content_type="application/json")
        dia.refresh_from_db()
        self.assertEqual((dia.todo_o_dia, dia.fim - dia.inicio), (False, timedelta(hours=1)))

    def test_clique_depois_das_23h_nao_cria_fim_antes_do_inicio(self):
        # mesma conta do JS do calendário: início 23:30 => fim 00:30 do dia seguinte
        inicio = datetime(2026, 10, 21, 23, 30)
        fim = inicio + timedelta(hours=1)
        r = self.client.post(reverse("compromisso-salvar"), self.modal(inicio=inicio.strftime("%Y-%m-%dT%H:%M"), fim=fim.strftime("%Y-%m-%dT%H:%M")))
        self.assertEqual(r.status_code, 200)


def evento(id_, titulo="Evento", dia=None, inicio=None, **extra):
    dia = dia or date.today() + timedelta(days=3)
    base = {"id": id_, "summary": titulo, "etag": f"e-{id_}", "updated": datetime.now(fuso_utc.utc).isoformat()}
    if inicio:
        base.update(start={"dateTime": inicio}, end={"dateTime": inicio})
    else:
        base.update(start={"date": dia.isoformat()}, end={"date": (dia + timedelta(days=1)).isoformat()})
    return {**base, **extra}


class GoogleFalso:
    """Lista paginada de eventos (nextPageToken) e erros configuráveis; registra as chamadas."""

    def __init__(self, paginas=(), erro=None):
        self.paginas, self.erro, self.chamadas = list(paginas), erro, []

    def __call__(self, metodo, url, cabecalhos=None, corpo=None, json_resposta=True):
        self.chamadas.append((metodo, url))
        if self.erro:
            raise self.erro
        if metodo == "GET":
            consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            indice = int(consulta.get("pageToken", ["0"])[0])
            itens = self.paginas[indice]
            dados = {"items": itens}
            if indice + 1 < len(self.paginas):
                dados["nextPageToken"] = str(indice + 1)
            return 200, {}, dados
        return 200, {}, {"id": "novo", "etag": "e"}


@override_settings(INTEGRACAO_CRYPTO_KEYS=CHAVE)
class GoogleAgendaTests(AgendaBase):
    def conectar(self, cliente=None, nome="Google"):
        return s.conectar(Req(self.gestor), cliente or self.a, "google_calendar", nome, {"access_token": "token-secreto", "refresh_token": "r"}, {"calendar_id": "primary"}, testar=False)

    def sincronizar(self, falso, conexao=None):
        with mock.patch.object(rede, "requisitar", side_effect=falso):
            return s.sincronizar_agenda(conexao or self.conexao)

    def setUp(self):
        super().setUp()
        self.conexao = self.conectar()

    def test_importa_todas_as_paginas_e_ignora_cancelados(self):
        pagina1 = [evento(f"g{i}", f"E{i}") for i in range(3)]
        pagina2 = [evento("g9", "Última"), evento("gc", "Cancelado", status="cancelled")]
        falso = GoogleFalso([pagina1, pagina2])
        resumo = self.sincronizar(falso)
        self.assertEqual(resumo["importados"], 4)
        self.assertEqual(set(Compromisso.objects.values_list("titulo", flat=True)), {"E0", "E1", "E2", "Última"})
        self.assertTrue(all(c.cliente_id == self.a.pk and c.origem == "externa" for c in Compromisso.objects.all()))

    def test_segunda_sincronizacao_nao_duplica_nem_reenvia(self):
        falso = GoogleFalso([[evento("g1", "Feira"), evento("g2", "Reunião", inicio="2026-11-03T14:00:00-03:00")]])
        self.sincronizar(falso)
        resumo = self.sincronizar(falso)
        self.assertEqual((Compromisso.objects.count(), resumo["importados"], resumo["criados"], resumo["atualizados"]), (2, 0, 0, 0))
        self.assertEqual([c for c in falso.chamadas if c[0] != "GET"], [])  # estável: nada é reescrito no Google

    def test_fuso_do_evento_e_respeitado(self):
        falso = GoogleFalso([[evento("g1", "Com fuso", inicio="2026-11-03T14:00:00-03:00"), {**evento("g2", "Só timeZone"), "start": {"dateTime": "2026-11-03T14:00:00", "timeZone": "America/Sao_Paulo"},
                                                                                             "end": {"dateTime": "2026-11-03T15:00:00", "timeZone": "America/Sao_Paulo"}}]])
        self.sincronizar(falso)
        horas = {c.titulo: c.inicio.astimezone(fuso_utc.utc).hour for c in Compromisso.objects.all()}
        self.assertEqual(horas, {"Com fuso": 17, "Só timeZone": 17})  # 14:00 em Brasília = 17:00 UTC

    def test_dia_inteiro_importado_volta_na_mesma_data(self):
        dia = date.today() + timedelta(days=5)
        self.sincronizar(GoogleFalso([[evento("g1", "Feriado", dia=dia)]]))
        c = Compromisso.objects.get()
        self.assertEqual((c.data_inicial, c.data_final, c.todo_o_dia), (dia, dia, True))

    def test_lista_truncada_nunca_apaga_compromissos(self):
        c = Compromisso.objects.create(cliente=self.a, titulo="Local", inicio=self.amanha, fim=self.amanha + timedelta(hours=1), origem="externa")
        ItemSincronizado.objects.create(conexao=self.conexao, tipo_local="compromisso", id_local=c.pk, id_externo="sumido", hash_conteudo="x")
        paginas = [[evento(f"p{i}-{j}") for j in range(2)] for i in range(21)]  # 21 páginas: acima do limite de 20
        resumo = self.sincronizar(GoogleFalso(paginas))
        self.assertEqual(resumo["erros"], 1)
        c.refresh_from_db()
        self.assertTrue(c.ativo)  # lista parcial não vira "foi excluído no Google"
        self.conexao.refresh_from_db()
        self.assertEqual(self.conexao.status, Conexao.ERRO)
        self.assertIn("mais de", self.conexao.ultimo_erro)

    def test_exclusao_no_google_desativa_so_o_compromisso_da_unidade(self):
        falso = GoogleFalso([[evento("g1", "Vai sumir")]])
        self.sincronizar(falso)
        alheio = Compromisso.objects.create(cliente=self.b, titulo="Da B", inicio=self.amanha, fim=self.amanha + timedelta(hours=1))
        self.sincronizar(GoogleFalso([[]]))  # o evento foi excluído no Google
        self.assertFalse(Compromisso.objects.get(titulo="Vai sumir").ativo)
        alheio.refresh_from_db()
        self.assertTrue(alheio.ativo)

    def test_mesmo_calendario_ligado_duas_vezes_nao_duplica(self):
        segunda = self.conectar(nome="Google 2")
        falso = GoogleFalso([[evento("g1", "Único")]])
        self.sincronizar(falso, self.conexao)
        resumo = self.sincronizar(falso, segunda)
        self.assertEqual((Compromisso.objects.filter(titulo="Único").count(), resumo["importados"]), (1, 0))
        self.assertEqual(ItemSincronizado.objects.filter(id_externo="g1").count(), 2)

    def test_clientes_nao_se_misturam(self):
        conexao_b = self.conectar(self.b, "Google B")
        self.sincronizar(GoogleFalso([[evento("g1", "Da A")]]), self.conexao)
        self.sincronizar(GoogleFalso([[evento("h1", "Da B")]]), conexao_b)
        self.assertEqual(set(Compromisso.objects.filter(cliente=self.a).values_list("titulo", flat=True)), {"Da A"})
        self.assertEqual(set(Compromisso.objects.filter(cliente=self.b).values_list("titulo", flat=True)), {"Da B"})

    def test_erros_do_google_viram_mensagem_util_sem_credencial(self):
        for status, trecho in ((401, "Reconecte a conta"), (403, "API do Google Agenda"), (404, "ID do calendário")):
            self.conexao.status = Conexao.CONECTADA
            self.conexao.save()
            self.sincronizar(GoogleFalso(erro=rede.ErroDeRede(f"HTTP {status}", status=status, temporario=False, detalhe="token-secreto")))
            self.conexao.refresh_from_db()
            self.assertEqual(self.conexao.status, Conexao.ERRO, status)
            self.assertIn(trecho, self.conexao.ultimo_erro)
            self.assertNotIn("token-secreto", self.conexao.ultimo_erro)

    def test_agenda_mostra_o_erro_so_a_quem_gere_integracoes(self):
        self.conexao.status, self.conexao.ultimo_erro = Conexao.ERRO, "Calendário não encontrado (404)."
        self.conexao.save()
        self.assertContains(self.client.get(reverse("compromisso-list")), "Calendário não encontrado")
        self.client.force_login(self.operador)
        self.assertNotContains(self.client.get(reverse("compromisso-list")), "Calendário não encontrado")


class ApiCompromissoTests(AgendaBase):
    def test_patch_nao_zera_concluido(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=self.gestor)[0].key}")
        r = api.post(f"{V1}/compromissos/", {"titulo": "Entrega", "inicio": self.amanha.isoformat(), "fim": (self.amanha + timedelta(hours=1)).isoformat(), "concluido": True}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        pk = r.json()["data"]["id"]
        self.assertTrue(r.json()["data"]["concluido"])
        r = api.patch(f"{V1}/compromissos/{pk}/", {"titulo": "Entrega 2"}, format="json")
        self.assertEqual((r.json()["data"]["titulo"], r.json()["data"]["concluido"]), ("Entrega 2", True))
