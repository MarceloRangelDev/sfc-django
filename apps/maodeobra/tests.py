"""Mão de obra por hora/dia: fórmulas, regras configuráveis, snapshot (resultados antigos não mudam), segmento, permissões, tenant,
relatório e API."""
import io
from datetime import date, time
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.modulo.models import Segmento
from apps.produto.models import Produto
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import calculo as c
from .models import Apontamento, Colaborador, ConfiguracaoMaoDeObra

D = Decimal
V1 = "/api/v1"
SEGUNDA, DOMINGO = date(2026, 10, 12), date(2026, 10, 18)


class CalculoTests(SimpleTestCase):
    def horas(self, ini, fim, pausa=0, valor=2000, data=SEGUNDA, **kw):
        return c.calcular_horas(ini, fim, pausa, valor, data, **kw)

    def test_exemplo_da_documentacao(self):
        r = self.horas(time(8, 0), time(17, 30))  # 9h30, valor R$ 20,00/h, jornada 8h, extra 50%
        self.assertEqual((r.minutos_trabalhados, r.minutos_normais, r.minutos_extra), (570, 480, 90))
        self.assertEqual((r.valor_normal, r.valor_extra, r.valor_total), (16000, 4500, 20500))

    def test_pausa_e_descontada(self):
        r = self.horas(time(8, 0), time(18, 0), pausa=60)  # 10h − 1h = 9h
        self.assertEqual((r.minutos_trabalhados, r.minutos_extra, r.valor_total), (540, 60, 16000 + 3000))

    def test_sem_hora_extra_dentro_da_jornada(self):
        r = self.horas(time(8, 0), time(12, 30))
        self.assertEqual((r.minutos_extra, r.valor_extra, r.valor_total), (0, 0, 9000))

    def test_virou_a_noite(self):
        r = self.horas(time(22, 0), time(6, 0))  # 8h
        self.assertEqual((r.minutos_brutos, r.valor_total), (480, 16000))

    def test_arredondamentos(self):
        for modo, esperado in ((c.ARRED_MINUTO, 53), (c.ARRED_QUARTO, 60), (c.ARRED_HORA, 60)):
            self.assertEqual(self.horas(time(8, 0), time(8, 53), arredondamento=modo).minutos_trabalhados, esperado, modo)
        self.assertEqual(self.horas(time(8, 0), time(9, 7), arredondamento=c.ARRED_QUARTO).minutos_trabalhados, 75)
        self.assertEqual(self.horas(time(8, 0), time(9, 7), arredondamento=c.ARRED_HORA).minutos_trabalhados, 120)
        self.assertEqual(self.horas(time(8, 0), time(9, 0), arredondamento=c.ARRED_HORA).minutos_trabalhados, 60)  # já cheio: não sobe

    def test_extra_configuravel_100_por_cento(self):
        r = self.horas(time(8, 0), time(18, 0), extra_percentual=D("100"))  # 2h extras a 100%
        self.assertEqual(r.valor_extra, 2 * 2000 * 2)

    def test_jornada_configuravel(self):
        r = self.horas(time(8, 0), time(14, 0), jornada_horas=D("4"))  # 6h com jornada de 4h
        self.assertEqual((r.minutos_normais, r.minutos_extra), (240, 120))

    def test_domingo_so_quando_ligado(self):
        sem = self.horas(time(8, 0), time(12, 0), data=DOMINGO)
        com = self.horas(time(8, 0), time(12, 0), data=DOMINGO, adicional_domingo=D("100"))
        self.assertEqual((sem.valor_total, com.valor_total), (8000, 16000))
        segunda = self.horas(time(8, 0), time(12, 0), data=SEGUNDA, adicional_domingo=D("100"))
        self.assertEqual(segunda.valor_total, 8000)
        extra_domingo = self.horas(time(8, 0), time(18, 0), data=DOMINGO, adicional_domingo=D("100"), extra_percentual=D("50"))
        self.assertEqual(extra_domingo.valor_total, 8 * 2000 * 2 + 2 * 2000 * 2)  # extras usam o maior adicional

    def test_arredondamento_do_valor_ao_centavo_meio_para_cima(self):
        # 1 minuto a R$ 0,30/h = 0,5 centavo -> 1
        self.assertEqual(self.horas(time(8, 0), time(8, 1), valor=30).valor_total, 1)

    def test_validacoes(self):
        for args in ((time(8, 0), time(9, 0), 60), (time(8, 0), time(9, 0), 90), (time(8, 0), time(9, 0), -1)):
            with self.assertRaises(RegraNegocioError):
                c.calcular_horas(*args, 2000, SEGUNDA)
        with self.assertRaises(RegraNegocioError):
            c.calcular_horas(time(8, 0), time(9, 0), 0, -5, SEGUNDA)
        with self.assertRaises(RegraNegocioError):
            c.arredondar_minutos(10, "xyz")

    def test_por_dia(self):
        self.assertEqual(c.calcular_dias(D("2"), 15000), 30000)
        self.assertEqual(c.calcular_dias(D("0.5"), 15001), 7501)  # 7500,5 -> 7501
        self.assertEqual(c.valor_dia_padrao(2000, D("8")), 16000)
        for dias in (0, -1, 32):
            with self.assertRaises(RegraNegocioError):
                c.calcular_dias(D(dias), 100)
        self.assertEqual(c.texto_horas(570), "9h30")


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = cliente
    u.profile.save()
    u.groups.add(Group.objects.get(name=grupo))
    return u


class Base(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        ambos = Segmento.objects.get(slug="ambos")
        self.cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Servicos A", segmento=ambos)
        self.outro = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Servicos B", segmento=ambos)
        self.produto_puro = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11555666000100", nome_razao_social="Padaria", segmento=Segmento.objects.get(slug="produto"))
        for cli in (self.cliente, self.outro):  # a padaria (segmento de produto) NÃO tem o módulo
            habilitar_modulo(cli, "mao-de-obra")
        self.gestor = usuario("gestor", self.cliente, "Gestor")
        self.operador = usuario("operador", self.cliente, "Operador")
        self.gestor_b = usuario("gestor_b", self.outro, "Gestor")
        self.gestor_padaria = usuario("gestor_p", self.produto_puro, "Gestor")
        self.ana = Colaborador.objects.create(cliente=self.cliente, nome="Ana", valor_hora=2000)
        self.client.force_login(self.gestor)

    def dados(self, **extra):
        return {"colaborador": self.ana.pk, "data": "2026-10-12", "tipo": "hora", "inicio": "08:00", "fim": "17:30", "pausa_minutos": 0, **extra}

    def lancar(self, **extra):
        return self.client.post(reverse("apontamento-create"), self.dados(**extra))


class TelasTests(Base):
    def test_lanca_por_hora_com_extra_e_audita(self):
        self.assertEqual(self.lancar().status_code, 302)
        a = Apontamento.objects.get()
        self.assertEqual((a.cliente_id, a.minutos_trabalhados, a.minutos_extra, a.valor_total, a.criado_por_id), (self.cliente.pk, 570, 90, 20500, self.gestor.pk))
        self.assertEqual((a.valor_hora_usado, a.jornada_usada, a.extra_percentual_usado, a.arredondamento_usado), (2000, D("8"), D("50"), "minuto"))
        r = RegistroAuditoria.objects.filter(modulo="maodeobra", acao="criar", tabela="maodeobra_apontamento").latest("id")
        self.assertEqual(r.registro_id, str(a.pk))

    def test_lanca_por_dia_e_diaria_propria(self):
        self.lancar(tipo="dia", dias="2", inicio="", fim="")
        self.assertEqual(Apontamento.objects.get().valor_total, 2 * 8 * 2000)  # diária = hora × jornada
        self.ana.valor_dia = 20000
        self.ana.save()
        self.lancar(tipo="dia", dias="1,5", inicio="", fim="")
        self.assertEqual(Apontamento.objects.latest("pk").valor_total, 30000)

    def test_validacoes_por_tipo(self):
        for extra in ({"inicio": ""}, {"fim": ""}, {"pausa_minutos": 700}, {"tipo": "dia", "dias": ""}, {"tipo": "dia", "dias": "0"}, {"tipo": "dia", "dias": "40"}, {"data": ""}, {"colaborador": ""}):
            r = self.lancar(**extra)
            self.assertEqual(r.status_code, 200, extra)
        self.assertFalse(Apontamento.objects.exists())

    def test_snapshot_valor_antigo_nao_muda_e_edicao_recalcula(self):
        self.lancar()
        a = Apontamento.objects.get()
        self.ana.valor_hora = 4000
        self.ana.save()
        config = ConfiguracaoMaoDeObra.da_unidade(self.cliente)
        config.extra_percentual = D("100")
        config.save()
        a.refresh_from_db()
        self.assertEqual(a.valor_total, 20500)  # nada mudou sozinho
        self.client.post(reverse("apontamento-edit", args=[a.pk]), self.dados())  # edição explícita = recálculo
        a.refresh_from_db()
        self.assertEqual((a.valor_hora_usado, a.valor_total), (4000, 8 * 4000 + int(1.5 * 4000 * 2)))
        registro = RegistroAuditoria.objects.filter(modulo="maodeobra", acao="editar").latest("id")
        self.assertIn("valor_total", {x["campo"] for x in registro.alteracoes})

    def test_configuracao_pela_tela_vale_para_os_proximos(self):
        self.client.post(reverse("mao-obra-config"), {"arredondamento": "quarto", "jornada_horas": "6", "extra_percentual": "100", "adicional_domingo": "0"})
        config = ConfiguracaoMaoDeObra.objects.get(cliente=self.cliente)
        self.assertEqual((config.arredondamento, config.jornada_horas, config.extra_percentual), ("quarto", D("6"), D("100")))
        self.assertFalse(ConfiguracaoMaoDeObra.objects.filter(cliente=self.outro).exists())
        self.lancar(inicio="08:00", fim="14:07")  # 6h07 -> 6h15, 15 min extras
        a = Apontamento.objects.get()
        self.assertEqual((a.minutos_trabalhados, a.minutos_extra), (375, 15))
        self.client.post(reverse("mao-obra-config"), {"arredondamento": "xyz", "jornada_horas": "6", "extra_percentual": "100", "adicional_domingo": "0"})
        self.assertEqual(ConfiguracaoMaoDeObra.objects.get(cliente=self.cliente).arredondamento, "quarto")

    def test_segmento_de_produto_nao_usa(self):
        self.client.force_login(self.gestor_padaria)
        for nome in ("apontamento-list", "colaborador-list", "mao-obra-relatorio", "mao-obra-config"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)

    def test_operador_nao_ve_valores_de_hora(self):
        self.client.force_login(self.operador)
        for nome in ("apontamento-list", "colaborador-list", "colaborador-create", "mao-obra-config", "mao-obra-relatorio", "apontamento-create"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)

    def test_tenant(self):
        self.lancar()
        a = Apontamento.objects.get()
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("apontamento-edit", args=[a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("colaborador-edit", args=[self.ana.pk])).status_code, 404)
        self.assertNotIn("Ana", self.client.get(reverse("apontamento-list")).content.decode())
        self.assertNotIn("Ana", self.client.get(reverse("colaborador-list")).content.decode())
        r = self.client.post(reverse("apontamento-create"), self.dados())  # colaborador de outra empresa
        self.assertEqual((r.status_code, Apontamento.objects.count()), (200, 1))

    def test_colaborador_crud_e_duplicado(self):
        self.client.post(reverse("colaborador-create"), {"nome": "Beto", "valor_hora": "25,50", "ativo": "on"})
        beto = Colaborador.objects.get(nome="Beto")
        self.assertEqual((beto.cliente_id, beto.valor_hora, beto.valor_dia), (self.cliente.pk, 2550, None))
        self.client.post(reverse("colaborador-create"), {"nome": "beto", "valor_hora": "10", "ativo": "on"})
        self.client.post(reverse("colaborador-create"), {"nome": "Zero", "valor_hora": "0", "ativo": "on"})
        self.assertEqual(Colaborador.objects.count(), 2)

    def test_servico_de_outra_empresa_e_rejeitado(self):
        categoria = __import__("apps.categoria.models", fromlist=["Categoria"]).Categoria.objects.create(cliente=self.outro, nome="X")
        alheio = Produto.objects.create(cliente=self.outro, nome="Servico B", categoria=categoria)
        self.assertEqual(self.lancar(servico=alheio.pk).status_code, 200)
        self.assertFalse(Apontamento.objects.exists())

    def test_relatorio_por_colaborador_exporta_e_so_do_tenant(self):
        self.lancar()
        self.lancar(data="2026-10-13", inicio="08:00", fim="12:00")
        outra = Colaborador.objects.create(cliente=self.outro, nome="Carla", valor_hora=9999)
        Apontamento.objects.create(cliente=self.outro, colaborador=outra, data=SEGUNDA, tipo="hora", inicio=time(8, 0), fim=time(9, 0))
        pagina = self.client.get(reverse("mao-obra-relatorio")).content.decode()
        self.assertIn("Ana", pagina)
        self.assertNotIn("Carla", pagina)
        self.assertIn("R$ 285,00", pagina)  # 205 + 80
        xlsx = self.client.get(reverse("mao-obra-relatorio"), {"export": "xlsx", "inicio": "2026-10-13"})
        planilha = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
        valores = [str(x.value) for linha in planilha.iter_rows() for x in linha if x.value]
        self.assertIn("R$ 80,00", valores)
        self.assertNotIn("Carla", valores)
        self.assertTrue(self.client.get(reverse("mao-obra-relatorio"), {"export": "pdf"}).content.startswith(b"%PDF"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="maodeobra", acao="exportar").exists())

    def test_excluir_apontamento_audita(self):
        self.lancar()
        a = Apontamento.objects.get()
        self.assertEqual(self.client.post(reverse("apontamento-excluir", args=[a.pk])).status_code, 403)  # Gestor não exclui (só Administrador)
        admin = usuario("admin", self.cliente, "Administrador")
        self.client.force_login(admin)
        self.client.post(reverse("apontamento-excluir", args=[a.pk]))
        self.assertFalse(Apontamento.objects.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="maodeobra", acao="excluir", registro_id=str(a.pk)).exists())


class APITests(Base):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o, self.api_b, self.api_p = (self.api(u) for u in (self.gestor, self.operador, self.gestor_b, self.gestor_padaria))

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def ok(self, r, codigo=200):
        self.assertEqual(r.status_code, codigo, r.content[:400])
        return r.json().get("data")

    def test_crud_de_colaborador_e_apontamento_calculado_no_servidor(self):
        col = self.ok(self.api_g.post(f"{V1}/colaboradores/", {"nome": "Duda", "valor_hora": 3000}, format="json"), 201)
        corpo = {"colaborador": col["id"], "data": "2026-10-12", "inicio": "08:00", "fim": "17:30", "valor_total": 1, "minutos_extra": 999}  # campos calculados são ignorados
        a = self.ok(self.api_g.post(f"{V1}/apontamentos/", corpo, format="json"), 201)
        self.assertEqual((a["minutos_trabalhados"], a["minutos_extra"], a["valor_total"], a["valor_hora_usado"]), (570, 90, 24000 + 6750, 3000))
        self.ok(self.api_g.patch(f"{V1}/colaboradores/{col['id']}/", {"valor_hora": 5000}, format="json"))
        self.assertEqual(self.ok(self.api_g.get(f"{V1}/apontamentos/{a['id']}/"))["valor_total"], 30750)  # snapshot
        b = self.ok(self.api_g.patch(f"{V1}/apontamentos/{a['id']}/", {"fim": "16:00"}, format="json"))  # editar recalcula
        self.assertEqual((b["valor_hora_usado"], b["minutos_trabalhados"], b["valor_total"]), (5000, 480, 40000))
        d = self.ok(self.api_g.post(f"{V1}/apontamentos/", {"colaborador": col["id"], "data": "2026-10-13", "tipo": "dia", "dias": "1.5"}, format="json"), 201)
        self.assertEqual(d["valor_total"], int(1.5 * 8 * 5000))
        lista = self.ok(self.api_g.get(f"{V1}/apontamentos/", {"colaborador": col["id"], "de": "2026-10-13"}))["itens"]
        self.assertEqual([x["id"] for x in lista], [d["id"]])

    def test_validacoes_e_regras_do_servidor(self):
        casos = [{"colaborador": self.ana.pk, "data": "2026-10-12", "inicio": "08:00"}, {"colaborador": self.ana.pk, "data": "2026-10-12", "inicio": "08:00", "fim": "09:00", "pausa_minutos": 60},
                 {"colaborador": self.ana.pk, "data": "2026-10-12", "tipo": "dia"}, {"colaborador": 99999, "data": "2026-10-12", "inicio": "08:00", "fim": "09:00"},
                 {"colaborador": self.ana.pk, "data": "data-ruim", "inicio": "08:00", "fim": "09:00"}]
        for corpo in casos:
            self.assertEqual(self.api_g.post(f"{V1}/apontamentos/", corpo, format="json").status_code, 400, corpo)
        self.assertFalse(Apontamento.objects.exists())

    def test_regras_pela_api_e_relatorio(self):
        self.assertEqual(self.ok(self.api_g.get(f"{V1}/mao-de-obra/regras/"))["extra_percentual"], "50.00")
        self.ok(self.api_g.patch(f"{V1}/mao-de-obra/regras/", {"arredondamento": "hora"}, format="json"))
        self.assertEqual(self.api_g.patch(f"{V1}/mao-de-obra/regras/", {"extra_percentual": "999"}, format="json").status_code, 400)
        a = self.ok(self.api_g.post(f"{V1}/apontamentos/", {"colaborador": self.ana.pk, "data": "2026-10-12", "inicio": "08:00", "fim": "09:10"}, format="json"), 201)
        self.assertEqual(a["minutos_trabalhados"], 120)
        rel = self.ok(self.api_g.get(f"{V1}/mao-de-obra/relatorio/", {"de": "2026-10-01"}))
        self.assertEqual((rel["total"], rel["itens"][0]["nome"]), (4000, "Ana"))

    def test_operador_segmento_e_tenant(self):
        self.assertEqual(self.api_o.get(f"{V1}/colaboradores/").status_code, 403)
        self.assertEqual(self.api_o.post(f"{V1}/apontamentos/", {}, format="json").status_code, 403)
        self.assertEqual(self.api_p.get(f"{V1}/colaboradores/").status_code, 403)  # segmento de produto: módulo não habilitado
        a = self.ok(self.api_g.post(f"{V1}/apontamentos/", {"colaborador": self.ana.pk, "data": "2026-10-12", "inicio": "08:00", "fim": "09:00"}, format="json"), 201)
        self.assertEqual(self.api_b.get(f"{V1}/apontamentos/{a['id']}/").status_code, 404)
        self.assertEqual(self.api_b.delete(f"{V1}/apontamentos/{a['id']}/").status_code, 403)  # Gestor não exclui
        admin_b = self.api(usuario("admin_b", self.outro, "Administrador"))
        self.assertEqual(admin_b.delete(f"{V1}/apontamentos/{a['id']}/").status_code, 404)  # Administrador de OUTRA unidade
        self.assertEqual(self.api_b.get(f"{V1}/colaboradores/{self.ana.pk}/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/apontamentos/").json()["data"]["itens"], [])
        self.assertEqual(APIClient().get(f"{V1}/apontamentos/").status_code, 401)
