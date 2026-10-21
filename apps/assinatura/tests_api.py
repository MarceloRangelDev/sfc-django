"""API da assinatura: autenticação, permissão, tenant, mensagem de bloqueio e o schema (Swagger)."""
from django.core.cache import cache
from rest_framework.test import APIClient

from .models import Assinatura
from .tests import Base


class AssinaturaApiTests(Base):
    def setUp(self):
        super().setUp()
        cache.clear()  # o limite do escopo "cadastro" (10/h) é compartilhado com outros testes da suíte completa

    def api(self, usuario):
        c = APIClient()
        c.force_authenticate(usuario)
        return c

    def test_exige_autenticacao(self):
        self.assertEqual(APIClient().get("/api/v1/assinatura/").status_code, 401)

    def test_gestor_le_apenas_a_propria_assinatura(self):
        self.assinatura()
        dados = self.api(self.gestor_a).get("/api/v1/assinatura/").json()["data"]
        self.assertEqual(dados["assinatura"]["plano"], "mensal")
        self.assertEqual({p["slug"]: p["valor"] for p in dados["planos"]}, {"mensal": 5990, "anual": 42900})
        self.assertIsNone(self.api(self.gestor_b).get("/api/v1/assinatura/").json()["data"]["assinatura"])  # outro tenant: nada

    def test_operador_sem_permissao_403(self):
        self.assertEqual(self.api(self.operador_a).get("/api/v1/assinatura/").status_code, 403)
        self.assertEqual(self.api(self.operador_a).post("/api/v1/assinatura/assinar/", {"plano": "mensal", "forma": "boleto"}, format="json").status_code, 403)

    def test_assinar_cancelar_e_validacao(self):
        api = self.api(self.gestor_a)
        self.assertEqual(api.post("/api/v1/assinatura/assinar/", {"plano": "mensal", "forma": "credit_card"}, format="json").status_code, 422)  # sem token
        self.assertEqual(api.post("/api/v1/assinatura/assinar/", {"plano": "mensal", "forma": "boleto"}, format="json").status_code, 201)
        self.assertEqual(api.post("/api/v1/assinatura/assinar/", {"plano": "anual", "forma": "boleto"}, format="json").status_code, 422)  # já existe
        self.assertEqual(api.post("/api/v1/assinatura/cancelar/").status_code, 200)
        self.assertEqual(Assinatura.objects.get().status, Assinatura.CANCELADA)

    def test_bloqueado_recebe_mensagem_clara_nos_outros_modulos(self):
        self.assinatura(status=Assinatura.BLOQUEADA)
        r = self.api(self.gestor_a).get("/api/v1/insumos/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("bloqueado por falta de pagamento", r.content.decode())
        self.assertEqual(self.api(self.gestor_a).get("/api/v1/assinatura/").status_code, 200)  # regularização segue acessível
        self.assertEqual(self.api(self.gestor_b).get("/api/v1/insumos/").status_code, 200)

    def test_schema_documenta_assinatura_e_kanban_sem_o_webhook(self):
        from django.contrib.auth.models import User

        admin = User.objects.create_superuser("root", "r@t.local", "SenhaForte123!")
        self.client.force_login(admin)
        r = self.client.get("/api/schema/")
        self.assertEqual(r.status_code, 200)
        texto = r.content.decode()
        self.assertIn("/api/v1/assinatura/assinar/", texto)
        self.assertIn("/api/v1/kanban/quadro/", texto)
        self.assertNotIn("webhook/", texto)
