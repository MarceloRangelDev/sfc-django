"""Kanban: acesso só do administrador global, isolamento de tenant, ordem ao mover, auditoria, API."""
from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente

from .models import Cartao, Coluna


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.groups.add(Group.objects.get(name=grupo))
    u.profile.cliente = cliente
    u.profile.save()
    return u


class KanbanBase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        self.admin = usuario("admin_a", self.a, "Administrador")
        self.admin_b = usuario("admin_b", self.b, "Administrador")
        self.gestor = usuario("gestor_a", self.a, "Gestor")
        # Mesmo com a permissão concedida a mão, quem não é administrador global é barrado no serviço.
        self.gestor.user_permissions.add(*Permission.objects.filter(content_type__app_label="kanban"))
        self.client.force_login(self.admin)

    def novo_cartao(self, titulo="Cartão", cliente=None, coluna=None):
        cliente = cliente or self.a
        coluna = coluna or Coluna.objects.filter(cliente=cliente).first() or Coluna.objects.create(cliente=cliente, nome="A fazer")
        return Cartao.objects.create(cliente=cliente, coluna=coluna, titulo=titulo, ordem=coluna.cartoes.count())


class AcessoTests(KanbanBase):
    def test_admin_ve_quadro_com_colunas_padrao(self):
        r = self.client.get(reverse("kanban"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(Coluna.objects.filter(cliente=self.a).values_list("nome", flat=True)), ["A fazer", "Em andamento", "Concluído"])

    def test_nao_admin_e_barrado_na_rota_e_no_servico_mesmo_com_permissao(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("kanban")).status_code, 403)
        self.assertEqual(self.client.post(reverse("kanban-coluna-criar"), {"nome": "X"}).status_code, 403)
        api = APIClient()
        api.force_authenticate(self.gestor)
        self.assertEqual(api.get("/api/v1/kanban/quadro/").status_code, 403)
        self.assertFalse(Coluna.objects.filter(nome="X").exists())

    def test_anonimo_vai_para_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("kanban")).status_code, 302)


class FluxoTests(KanbanBase):
    def setUp(self):
        super().setUp()
        self.client.get(reverse("kanban"))  # cria as colunas padrão
        self.fazer, self.andamento, self.feito = Coluna.objects.filter(cliente=self.a)

    def test_criar_editar_excluir_cartao_com_auditoria(self):
        r = self.client.post(reverse("kanban-cartao-criar"), {"coluna": self.fazer.pk, "titulo": " Ligar ", "responsavel": self.admin.pk, "prazo": "2026-12-01"})
        self.assertTrue(r.json()["success"])
        c = Cartao.objects.get()
        self.assertEqual((c.titulo, c.responsavel, c.cliente), ("Ligar", self.admin, self.a))
        self.client.post(reverse("kanban-cartao-editar", args=[c.pk]), {"titulo": "Ligar já"})
        c.refresh_from_db()
        self.assertEqual(c.titulo, "Ligar já")
        self.client.post(reverse("kanban-cartao-excluir", args=[c.pk]))
        self.assertFalse(Cartao.objects.exists())
        self.assertEqual(set(RegistroAuditoria.objects.filter(modulo="kanban").values_list("acao", flat=True)), {"criar", "editar", "excluir"})

    def test_validacoes(self):
        self.assertEqual(self.client.post(reverse("kanban-cartao-criar"), {"coluna": self.fazer.pk, "titulo": " "}).status_code, 400)
        self.assertEqual(self.client.post(reverse("kanban-cartao-criar"), {"coluna": self.fazer.pk, "titulo": "x" * 151}).status_code, 400)
        self.assertEqual(self.client.post(reverse("kanban-cartao-criar"), {"coluna": self.fazer.pk, "titulo": "ok", "prazo": "31/02"}).status_code, 400)
        outro = usuario("outro_b", self.b, "Gestor")  # responsável de outro tenant é recusado
        self.assertEqual(self.client.post(reverse("kanban-cartao-criar"), {"coluna": self.fazer.pk, "titulo": "ok", "responsavel": outro.pk}).status_code, 400)

    def test_mover_reordena_origem_e_destino_e_audita(self):
        c1, c2, c3 = (self.novo_cartao(t, coluna=self.fazer) for t in ("1", "2", "3"))
        r = self.client.post(reverse("kanban-cartao-mover", args=[c1.pk]), {"coluna": self.andamento.pk, "posicao": 0})
        self.assertTrue(r.json()["success"])
        self.assertEqual(list(self.fazer.cartoes.values_list("titulo", "ordem")), [("2", 0), ("3", 1)])
        self.client.post(reverse("kanban-cartao-mover", args=[c3.pk]), {"coluna": self.andamento.pk, "posicao": 0})
        self.assertEqual(list(self.andamento.cartoes.values_list("titulo", "ordem")), [("3", 0), ("1", 1)])
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="kanban", acao="editar").count(), 2)

    def test_isolamento_de_tenant(self):
        coluna_b = Coluna.objects.create(cliente=self.b, nome="B")
        cartao_b = self.novo_cartao("Segredo", cliente=self.b, coluna=coluna_b)
        for nome, args, dados in (
            ("kanban-cartao-editar", [cartao_b.pk], {"titulo": "hack"}), ("kanban-cartao-excluir", [cartao_b.pk], {}),
            ("kanban-cartao-mover", [cartao_b.pk], {"coluna": self.fazer.pk, "posicao": 0}), ("kanban-coluna-excluir", [coluna_b.pk], {}),
            ("kanban-coluna-renomear", [coluna_b.pk], {"nome": "hack"}),
        ):
            self.assertEqual(self.client.post(reverse(nome, args=args), dados).status_code, 404, nome)
        # mover cartão próprio para coluna de outro tenant também é 404
        c = self.novo_cartao("meu", coluna=self.fazer)
        self.assertEqual(self.client.post(reverse("kanban-cartao-mover", args=[c.pk]), {"coluna": coluna_b.pk, "posicao": 0}).status_code, 404)
        cartao_b.refresh_from_db()
        self.assertEqual((cartao_b.titulo, cartao_b.coluna), ("Segredo", coluna_b))
        self.assertNotContains(self.client.get(reverse("kanban")), "Segredo")

    def test_coluna_com_cartao_nao_exclui_e_vazia_sim(self):
        self.novo_cartao(coluna=self.fazer)
        self.assertEqual(self.client.post(reverse("kanban-coluna-excluir", args=[self.fazer.pk])).status_code, 400)
        self.assertTrue(self.client.post(reverse("kanban-coluna-excluir", args=[self.feito.pk])).json()["success"])
        self.assertEqual(list(Coluna.objects.filter(cliente=self.a).values_list("ordem", flat=True)), [0, 1])

    def test_get_nao_executa_acao(self):
        self.assertEqual(self.client.get(reverse("kanban-coluna-criar")).status_code, 405)


class ApiTests(KanbanBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def test_fluxo_completo(self):
        quadro = self.api.get("/api/v1/kanban/quadro/").json()["data"]
        self.assertEqual([c["nome"] for c in quadro], ["A fazer", "Em andamento", "Concluído"])
        r = self.api.post("/api/v1/kanban/cartoes/", {"coluna": quadro[0]["id"], "titulo": "API"}, format="json")
        self.assertEqual(r.status_code, 201)
        cid = r.json()["data"]["id"]
        self.assertEqual(self.api.post(f"/api/v1/kanban/cartoes/{cid}/mover/", {"coluna": quadro[1]["id"], "posicao": 0}, format="json").status_code, 200)
        self.assertEqual(self.api.patch(f"/api/v1/kanban/cartoes/{cid}/", {"titulo": "Novo"}, format="json").status_code, 200)
        self.assertEqual(self.api.post("/api/v1/kanban/cartoes/", {"coluna": quadro[0]["id"], "titulo": ""}, format="json").status_code, 422)
        self.assertEqual(self.api.delete(f"/api/v1/kanban/cartoes/{cid}/").status_code, 200)

    def test_outro_tenant_recebe_404(self):
        outro = APIClient()
        outro.force_authenticate(self.admin_b)
        outro.get("/api/v1/kanban/quadro/")
        cartao = self.novo_cartao("meu")
        self.assertEqual(outro.patch(f"/api/v1/kanban/cartoes/{cartao.pk}/", {"titulo": "x"}, format="json").status_code, 404)
        self.assertEqual(outro.delete(f"/api/v1/kanban/cartoes/{cartao.pk}/").status_code, 404)

    def test_sem_autenticacao_401(self):
        self.assertEqual(APIClient().get("/api/v1/kanban/quadro/").status_code, 401)
