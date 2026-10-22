"""Agenda: filtro de situação (em aberto / atrasados / concluídos) na tela, no calendário (JSON) e na API, respeitando o
escopo de unidades e as permissões. Os filtros anteriores (tipo, texto, status ativo/inativo) continuam valendo."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.cliente.models import Cliente
from web_project.testing import habilitar_modulo

from .models import Compromisso
from .services import filtrar_por_situacao

V1 = "/api/v1"


class AgendaFiltrosBase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        for c in (self.a, self.b):
            c.perfis.set([Group.objects.get(name="Gestor")])
            habilitar_modulo(c, "agenda")
        self.gestor = self._usuario("gestor_a", self.a, "Gestor")
        self.gestor_b = self._usuario("gestor_b", self.b, "Gestor")
        self.visualizador = self._usuario("visual_a", self.a, "Visualizador")
        agora = timezone.now()
        # a: futuro aberto, atrasado aberto, passado concluído, futuro concluído, atrasado inativo; b: um atrasado aberto
        self.futuro = self.criar("Futuro aberto", agora + timedelta(days=2))
        self.atrasado = self.criar("Atrasado aberto", agora - timedelta(days=3))
        self.feito_passado = self.criar("Feito passado", agora - timedelta(days=4), concluido=True)
        self.feito_futuro = self.criar("Feito futuro", agora + timedelta(days=1), concluido=True)
        self.inativo = self.criar("Atrasado inativo", agora - timedelta(days=5), ativo=False)
        self.alheio = self.criar("Atrasado da B", agora - timedelta(days=3), cliente=self.b)

    def _usuario(self, nome, cliente, grupo):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
        u.groups.add(Group.objects.get(name=grupo))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def criar(self, titulo, inicio, cliente=None, **extra):
        return Compromisso.objects.create(cliente=cliente or self.a, titulo=titulo, tipo="tarefa", inicio=inicio, fim=inicio + timedelta(hours=1), **extra)


class ServicoDeSituacaoTests(AgendaFiltrosBase):
    def titulos(self, situacao):
        return set(filtrar_por_situacao(Compromisso.objects.filter(cliente=self.a), situacao).values_list("titulo", flat=True))

    def test_criterios(self):
        self.assertEqual(self.titulos("aberto"), {"Futuro aberto", "Atrasado aberto", "Atrasado inativo"})
        self.assertEqual(self.titulos("atrasado"), {"Atrasado aberto", "Atrasado inativo"})
        self.assertEqual(self.titulos("concluido"), {"Feito passado", "Feito futuro"})

    def test_valor_vazio_ou_desconhecido_nao_filtra(self):
        todos = Compromisso.objects.filter(cliente=self.a).count()
        for valor in (None, "", "qualquer"):
            self.assertEqual(filtrar_por_situacao(Compromisso.objects.filter(cliente=self.a), valor).count(), todos)


class TelaTests(AgendaFiltrosBase):
    def lista(self, **params):
        resposta = self.client.get(reverse("compromisso-list"), params)
        self.assertEqual(resposta.status_code, 200)
        return {c.titulo for c in resposta.context["compromissos"]}

    def test_filtros_da_tabela_e_escopo(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.lista(), {"Futuro aberto", "Atrasado aberto", "Feito passado", "Feito futuro"})  # sem filtro: como antes (ativos)
        self.assertEqual(self.lista(situacao="aberto"), {"Futuro aberto", "Atrasado aberto"})
        self.assertEqual(self.lista(situacao="atrasado"), {"Atrasado aberto"})
        self.assertEqual(self.lista(situacao="concluido"), {"Feito passado", "Feito futuro"})
        self.assertNotIn("Atrasado da B", self.lista(situacao="atrasado"))  # nunca de outra empresa

    def test_combina_com_status_tipo_e_texto(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.lista(situacao="atrasado", status="todos"), {"Atrasado aberto", "Atrasado inativo"})
        self.assertEqual(self.lista(situacao="aberto", q="Futuro"), {"Futuro aberto"})
        self.assertEqual(self.lista(situacao="aberto", tipo="reserva"), set())

    def test_situacao_invalida_e_ignorada_e_seletor_reflete_a_escolha(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.lista(situacao="<script>"), self.lista())
        resposta = self.client.get(reverse("compromisso-list"), {"situacao": "atrasado"})
        self.assertContains(resposta, '<option value="atrasado" selected>')

    def test_outra_empresa_ve_so_o_seu(self):
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.lista(situacao="atrasado"), {"Atrasado da B"})

    def test_visualizador_pode_filtrar_a_propria_unidade(self):
        self.client.force_login(self.visualizador)
        self.assertEqual(self.lista(situacao="concluido"), {"Feito passado", "Feito futuro"})

    def test_anonimo_vai_para_o_login(self):
        self.assertEqual(self.client.get(reverse("compromisso-list"), {"situacao": "aberto"}).status_code, 302)

    def test_calendario_json_aceita_o_mesmo_filtro(self):
        self.client.force_login(self.gestor)
        inicio = (timezone.now() - timedelta(days=10)).isoformat()
        fim = (timezone.now() + timedelta(days=10)).isoformat()

        def eventos(**extra):
            resposta = self.client.get(reverse("compromisso-eventos"), {"start": inicio, "end": fim, **extra})
            return {e["title"] for e in resposta.json()}

        self.assertEqual(eventos(), {"Futuro aberto", "Atrasado aberto", "Feito passado", "Feito futuro"})
        self.assertEqual(eventos(situacao="atrasado"), {"Atrasado aberto"})
        self.assertEqual(eventos(situacao="concluido"), {"Feito passado", "Feito futuro"})


class ApiTests(AgendaFiltrosBase):
    def api(self, user):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=user)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def titulos(self, api, **params):
        resposta = api.get(f"{V1}/compromissos/", params)
        self.assertEqual(resposta.status_code, 200, resposta.content[:300])
        return {c["titulo"] for c in resposta.json()["data"]["itens"]}

    def test_filtros_da_api_e_escopo(self):
        api = self.api(self.gestor)
        self.assertEqual(self.titulos(api, situacao="aberto"), {"Futuro aberto", "Atrasado aberto"})
        self.assertEqual(self.titulos(api, situacao="atrasado"), {"Atrasado aberto"})
        self.assertEqual(self.titulos(api, situacao="concluido"), {"Feito passado", "Feito futuro"})
        self.assertEqual(self.titulos(api, situacao="atrasado", ativo="todos"), {"Atrasado aberto", "Atrasado inativo"})
        self.assertEqual(self.titulos(self.api(self.gestor_b), situacao="atrasado"), {"Atrasado da B"})

    def test_janela_de_datas_e_tipo(self):
        api = self.api(self.gestor)
        hoje = timezone.localdate()
        self.assertEqual(self.titulos(api, inicio_de=hoje.isoformat()), {"Futuro aberto", "Feito futuro"})
        self.assertEqual(self.titulos(api, inicio_ate=(hoje - timedelta(days=2)).isoformat()), {"Atrasado aberto", "Feito passado"})
        self.assertEqual(self.titulos(api, tipo="reserva"), set())

    def test_visualizador_le_e_anonimo_nao(self):
        self.assertEqual(self.titulos(self.api(self.visualizador), situacao="concluido"), {"Feito passado", "Feito futuro"})
        self.assertEqual(APIClient().get(f"{V1}/compromissos/").status_code, 401)

    def test_situacao_aparece_no_swagger(self):
        from drf_spectacular.generators import SchemaGenerator

        esquema = SchemaGenerator().get_schema(request=None, public=True)
        parametros = {p["name"]: p for p in esquema["paths"]["/api/v1/compromissos/"]["get"]["parameters"]}
        self.assertEqual(parametros["situacao"]["schema"]["enum"], ["aberto", "atrasado", "concluido"])
        self.assertIn("tipo", parametros)
