"""Integrações liberadas por segmento: o cadastro do segmento define quais provedores a unidade pode conectar."""
from django.contrib.auth.models import User
from django.urls import reverse

from apps.modulo.models import Segmento
from web_project.regras import RegraNegocioError

from . import services as s
from .adaptadores import REGISTRO
from .tests import Base, SEGREDO, cfg


@cfg
class IntegracoesPorSegmentoTests(Base):
    def setUp(self):
        super().setUp()
        self.segmento = Segmento.objects.create(nome="Seg X", slug="seg-x", tipo_precificacao=Segmento.TIPO_AMBOS)
        self.cliente.segmento = self.segmento
        self.cliente.save()
        self.gestor.profile.refresh_from_db()

    def bloquear(self, *chaves):
        self.segmento.integracoes_bloqueadas = list(chaves)
        self.segmento.save()

    def test_padrao_e_tudo_liberado(self):
        self.assertEqual(self.segmento.integracoes_bloqueadas, [])
        self.assertTrue(all(self.segmento.integracao_liberada(c) for c in REGISTRO))
        self.assertTrue(s.provedor_liberado(self.cliente, "instagram"))
        # unidade sem segmento também segue liberada (comportamento anterior)
        self.assertTrue(s.provedor_liberado(self.outro, "instagram"))

    def test_servico_recusa_provedor_bloqueado(self):
        self.bloquear("whatsapp_evolution")
        with self.assertRaises(RegraNegocioError) as erro:
            s.conectar(self.req, self.cliente, "whatsapp_evolution", "Nova", {"segredo": SEGREDO}, {})
        self.assertIn("não está liberada", str(erro.exception))
        # a conexão de teste (outro provedor) continua podendo ser criada
        self.assertEqual(self.conexao().provedor, "teste")

    def test_tela_esconde_cartao_e_bloqueia_a_rota(self):
        self.client.force_login(self.gestor)
        self.bloquear("whatsapp_evolution")
        pagina = self.client.get(reverse("integracoes"))
        self.assertEqual(pagina.status_code, 200)
        self.assertNotContains(pagina, reverse("integracao-nova", args=["whatsapp_evolution"]))
        self.assertContains(pagina, reverse("integracao-nova", args=["whatsapp_cloud"]))
        self.assertContains(pagina, "não estão liberadas para o segmento")
        self.assertEqual(self.client.get(reverse("integracao-nova", args=["whatsapp_evolution"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("integracao-nova", args=["whatsapp_cloud"])).status_code, 200)

    def test_templates_do_whatsapp_seguem_o_whatsapp_oficial(self):
        self.client.force_login(self.gestor)
        self.assertContains(self.client.get(reverse("integracoes")), "btn-templates-whatsapp")
        self.bloquear("whatsapp_cloud")
        self.assertNotContains(self.client.get(reverse("integracoes")), "btn-templates-whatsapp")

    def test_formulario_do_segmento_grava_o_que_foi_desmarcado(self):
        admin = User.objects.create_superuser("root", "root@t.local", "SenhaForte123!")
        admin.profile.cliente = self.cliente
        admin.profile.save()
        self.client.force_login(admin)
        url = reverse("segmento-edit", args=[self.segmento.pk])
        tela = self.client.get(url)
        self.assertContains(tela, 'name="integracoes"')
        dados = {"nome": "Seg X", "slug": "seg-x", "descricao": "", "tipo_precificacao": Segmento.TIPO_AMBOS, "ativo": "on",
                 "integracoes": ["instagram", "google_calendar"]}
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.segmento.refresh_from_db()
        self.assertTrue(self.segmento.integracao_liberada("instagram"))
        self.assertTrue(self.segmento.integracao_liberada("google_calendar"))
        self.assertFalse(self.segmento.integracao_liberada("whatsapp_cloud"))
        self.assertFalse(self.segmento.integracao_liberada("icloud_calendar"))
        # reabrir a tela mostra marcados só os liberados; salvar tudo marcado libera de novo
        dados["integracoes"] = [c for c in REGISTRO if c != "teste"]
        self.client.post(url, dados)
        self.segmento.refresh_from_db()
        self.assertEqual(self.segmento.integracoes_bloqueadas, [])
