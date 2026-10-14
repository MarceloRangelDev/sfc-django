"""Etiquetas com QR Code, UUID estável do insumo, rota do QR (login + tenant) e histórico do insumo."""
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.insumo.historico import variacao_percentual
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .etiquetas import gerar_pdf

HOJE = timezone.localdate()
V1 = "/api/v1"


def novo_usuario(sufixo, cnpj, apps=("estoque", "insumo")):
    cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=f"Empresa {sufixo}")
    for modulo in ("insumo", "estoque"):
        habilitar_modulo(cliente, modulo)
    grupo = Group.objects.create(name=f"G {sufixo}")
    grupo.permissions.set(Permission.objects.filter(content_type__app_label__in=apps))
    usuario = User.objects.create_user(username=f"u_{sufixo}", email=f"{sufixo}@t.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)
    return cliente, usuario


class UuidTests(TestCase):
    def test_cada_insumo_tem_uuid_proprio_e_estavel(self):
        cliente, _ = novo_usuario("a", "11222333000181")
        a, b = novo_insumo(cliente, "A", "kg", "1", 100), novo_insumo(cliente, "B", "kg", "1", 100)
        self.assertNotEqual(a.uuid, b.uuid)
        antes = a.uuid
        a.nome = "A2"
        a.preco_pago = 200
        a.save()
        a.refresh_from_db()
        self.assertEqual(a.uuid, antes)
        self.assertEqual(a.uuid.version, 4)

    def test_uuid_nao_entra_na_auditoria_nem_e_editavel_pela_api(self):
        cliente, usuario = novo_usuario("a", "11222333000181")
        insumo = novo_insumo(cliente, "A", "kg", "1", 100)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")
        original = insumo.uuid
        r = api.patch(f"{V1}/insumos/{insumo.pk}/", {"uuid": str(uuid.uuid4()), "dias_validade": 5}, format="json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        insumo.refresh_from_db()
        self.assertEqual((insumo.uuid, insumo.dias_validade), (original, 5))
        self.assertEqual(r.json()["data"]["uuid"], str(original))


class PdfTests(TestCase):
    DADOS = {"nome": "Queijo Mussarela", "lote": "L1", "emissao": HOJE, "validade": HOJE + timedelta(days=5), "url": "https://sfc.test/i/abc"}

    def test_a4_e_termica_geram_pdf_com_paginas_certas(self):
        a4 = gerar_pdf(self.DADOS, 30, "a4")  # 24 por folha -> 2 páginas
        termica = gerar_pdf(self.DADOS, 3, "termica")  # 1 por página
        self.assertTrue(a4.startswith(b"%PDF") and termica.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", a4)), 2)
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", termica)), 3)
        self.assertIn(b"/MediaBox [ 0 0 141.7", termica)  # 50 mm de largura


class EtiquetaWebTests(TestCase):
    def setUp(self):
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.insumo = novo_insumo(self.cliente, "Queijo", "kg", "1", 1000)
        self.client.force_login(self.user)

    def gerar(self, **extra):
        dados = {"insumo": self.insumo.pk, "emissao": HOJE.isoformat(), "modo_validade": "digitada",
                 "validade": (HOJE + timedelta(days=3)).isoformat(), "copias": 2, "formato": "a4", **extra}
        return self.client.post(reverse("estoque-etiquetas"), dados)

    def test_gera_pdf_inline_e_audita(self):
        r = self.gerar(lote="L-77")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("inline", r["Content-Disposition"])
        registro = RegistroAuditoria.objects.filter(modulo="estoque", acao="exportar").latest("id")
        self.assertIn("2 do insumo", registro.descricao)

    def test_validade_calculada_usa_dias_do_insumo(self):
        # sem dias_validade: erro (volta ao formulário)
        r = self.gerar(modo_validade="calculada", validade="")
        self.assertEqual(r["Content-Type"].split(";")[0], "text/html")
        self.assertIn("não tem validade padrão", r.content.decode())
        self.insumo.dias_validade = 4
        self.insumo.save()
        self.assertEqual(self.gerar(modo_validade="calculada", validade="")["Content-Type"], "application/pdf")

    def test_validacoes(self):
        for extra in ({"validade": ""}, {"validade": (HOJE - timedelta(days=1)).isoformat()}, {"copias": 0}, {"copias": 201}, {"formato": "xyz"}):
            r = self.gerar(**extra)
            self.assertEqual(r["Content-Type"].split(";")[0], "text/html", extra)

    def test_insumo_de_outro_tenant_e_rejeitado(self):
        alheio = novo_insumo(self.outro, "Alheio", "kg", "1", 100)
        r = self.gerar(insumo=alheio.pk)
        self.assertEqual(r["Content-Type"].split(";")[0], "text/html")  # campo recusa o id
        self.assertFalse(RegistroAuditoria.objects.filter(acao="exportar", registro_id=str(alheio.pk)).exists())

    def test_sem_permissao_de_estoque_403(self):
        _, sem = novo_usuario("c", "11555666000100", apps=("insumo",))
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("estoque-etiquetas")).status_code, 403)

    def test_qr_aponta_para_url_com_uuid_sem_dados_sensiveis(self):
        from .etiquetas import url_do_qr

        class R:
            def build_absolute_uri(self, p): return "https://sfc.exemplo.com/"

        with self.settings(BASE_URL=""):
            url = url_do_qr(R(), self.insumo)
        self.assertEqual(url, f"https://sfc.exemplo.com/i/{self.insumo.uuid}")
        for sensivel in (str(self.insumo.pk), self.insumo.nome, "1000"):
            self.assertNotIn(f"/i/{sensivel}", url)

    def test_api_etiqueta(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=self.user)[0].key}")
        r = api.get(f"{V1}/estoque/saldos/{self.insumo.pk}/etiqueta/", {"validade": (HOJE + timedelta(days=2)).isoformat(), "copias": 3, "formato": "termica"})
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(api.get(f"{V1}/estoque/saldos/{self.insumo.pk}/etiqueta/").status_code, 400)  # falta a validade
        api_b = APIClient()
        api_b.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=self.user_b)[0].key}")
        self.assertEqual(api_b.get(f"{V1}/estoque/saldos/{self.insumo.pk}/etiqueta/", {"validade": HOJE.isoformat()}).status_code, 404)


class RotaDoQrTests(TestCase):
    def setUp(self):
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.insumo = novo_insumo(self.cliente, "Queijo", "kg", "1", 1000)

    def test_anonimo_vai_para_o_login(self):
        r = self.client.get(reverse("insumo-uuid", args=[self.insumo.uuid]))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_mesmo_tenant_abre_o_insumo_no_estoque(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("insumo-uuid", args=[self.insumo.uuid]))
        self.assertRedirects(r, reverse("estoque-insumo", args=[self.insumo.pk]), fetch_redirect_response=False)

    def test_outro_tenant_e_uuid_inexistente_dao_o_mesmo_404(self):
        self.client.force_login(self.user_b)
        alheio = self.client.get(reverse("insumo-uuid", args=[self.insumo.uuid]))
        inexistente = self.client.get(reverse("insumo-uuid", args=[uuid.uuid4()]))
        self.assertEqual((alheio.status_code, inexistente.status_code), (404, 404))

    def test_sem_modulo_de_estoque_403(self):
        _, sem = novo_usuario("c", "11555666000100", apps=("insumo",))
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("insumo-uuid", args=[self.insumo.uuid])).status_code, 403)


class HistoricoTests(TestCase):
    def setUp(self):
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.client.force_login(self.user)
        self.client.post(reverse("insumo-create"), {"nome": "Queijo", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": "10,00", "ativo": "on"})
        self.insumo = Insumo.objects.get(nome="Queijo")
        self.client.post(reverse("insumo-edit", args=[self.insumo.pk]), {"nome": "Queijo", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": "12,00", "ativo": "on"})

    def test_mostra_alteracoes_com_variacao(self):
        pagina = self.client.get(reverse("insumo-historico", args=[self.insumo.pk]), {"campo": "preco_pago"}).content.decode()
        self.assertIn("R$ 10,00", pagina)
        self.assertIn("R$ 12,00", pagina)
        self.assertIn("+20,0%", pagina)

    def test_nao_mostra_registros_de_outro_insumo_nem_exportacoes(self):
        outro = novo_insumo(self.cliente, "Outro", "kg", "1", 500)
        RegistroAuditoria.objects.create(usuario=self.user, cliente=self.cliente, acao="exportar", modulo="estoque", tabela="insumo_insumo", registro_id=str(self.insumo.pk), descricao="Etiquetas XYZ")
        pagina = self.client.get(reverse("insumo-historico", args=[self.insumo.pk])).content.decode()
        self.assertNotIn("Outro", pagina)
        self.assertNotIn("Etiquetas XYZ", pagina)
        self.assertEqual(outro.pk != self.insumo.pk, True)

    def test_outro_tenant_recebe_404(self):
        self.client.force_login(self.user_b)
        self.assertEqual(self.client.get(reverse("insumo-historico", args=[self.insumo.pk])).status_code, 404)

    def test_variacao_percentual(self):
        self.assertEqual(variacao_percentual("R$ 10,00", "R$ 12,50"), "+25,0%")
        self.assertEqual(variacao_percentual("R$ 10,00", "R$ 5,00"), "-50,0%")
        self.assertEqual(variacao_percentual("Quilo", "Gramas"), "")
        self.assertEqual(variacao_percentual("0", "5"), "")
        self.assertEqual(variacao_percentual("1.000", "2.000"), "+100,0%")
