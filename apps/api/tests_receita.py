"""Receita e observações de Processados/Fichas (Fase 1): permissão `ver_receita`, limites, auditoria sem
conteúdo, exportação individual (PDF/Excel) com a escolha de incluir a receita, e isolamento entre tenants."""
import io

import openpyxl
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.insumo.tests import medida
from apps.processado.models import Processado

from .tests import V1
from .tests_cadastros import CadastrosBase

RECEITA = "1. Bata a farinha.\n2. Asse por 30 min."
OBS = "Servir morno <b>sem HTML</b>"


class ReceitaAPITests(CadastrosBase):
    def processado(self, api=None, **extra):
        corpo = {
            "nome": "Massa", "rendimento_quantidade": "1", "rendimento_medida": medida("kg").pk,
            "itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "1"}], **extra,
        }
        return self.post("processados", corpo, api)

    def tirar_permissao(self):
        """Remove `ver_receita` do perfil do usuário A (processado e ficha)."""
        perfil = self.usuario_a.groups.first()
        perfil.permissions.remove(*Permission.objects.filter(codename="ver_receita"))

    def test_cria_le_e_atualiza_com_permissao(self):
        criado = self.processado(receita=RECEITA, observacoes=OBS)
        self.assertEqual((criado["receita"], criado["observacoes"]), (RECEITA, OBS))
        # PATCH sem os campos preserva o texto; com os campos, altera.
        r = self.ok(self.api_a.patch(f"{V1}/processados/{criado['id']}/", {"nome": "Massa 2"}, format="json"))["data"]
        self.assertEqual(r["receita"], RECEITA)
        r = self.ok(self.api_a.patch(f"{V1}/processados/{criado['id']}/", {"receita": "Nova"}, format="json"))["data"]
        self.assertEqual((r["receita"], r["observacoes"]), ("Nova", OBS))

    def test_sem_permissao_nao_ve_nao_grava_e_nao_perde_o_texto(self):
        criado = self.processado(receita=RECEITA)
        self.tirar_permissao()
        lido = self.ok(self.api_a.get(f"{V1}/processados/{criado['id']}/"))["data"]
        self.assertNotIn("receita", lido)
        self.assertNotIn("observacoes", lido)
        self.assertNotIn("receita", self.lista("processados")[0])
        self.erro(self.api_a.patch(f"{V1}/processados/{criado['id']}/", {"receita": "invadi"}, format="json"), 403)
        # editar outro campo sem permissão não apaga a receita existente
        self.ok(self.api_a.patch(f"{V1}/processados/{criado['id']}/", {"nome": "Outro nome"}, format="json"))
        self.assertEqual(Processado.objects.get(pk=criado["id"]).receita, RECEITA)

    def test_limites_validados_no_servidor(self):
        corpo = {"nome": "X", "rendimento_quantidade": "1", "rendimento_medida": medida("kg").pk,
                 "itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "1"}]}
        self.erro(self.api_a.post(f"{V1}/processados/", {**corpo, "receita": "a" * 5001}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/processados/", {**corpo, "observacoes": "a" * 1001}, format="json"), 400)
        self.ok(self.api_a.post(f"{V1}/processados/", {**corpo, "receita": "a" * 5000, "observacoes": "b" * 1000}, format="json"), 201)

    def test_auditoria_registra_mudanca_sem_o_conteudo(self):
        criado = self.processado(receita=RECEITA)
        self.ok(self.api_a.patch(f"{V1}/processados/{criado['id']}/", {"receita": "Segredo novo"}, format="json"))
        registro = RegistroAuditoria.objects.filter(modulo="processado", acao="editar").latest("id")
        alteracao = next(a for a in registro.alteracoes if a["campo"] == "receita")
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("(conteúdo omitido)", "(conteúdo omitido)"))
        self.assertNotIn("Segredo", registro.descricao)
        self.assertNotIn("farinha", registro.descricao.lower().replace("farinha:", ""))  # nem o texto antigo

    def test_ficha_tambem_tem_receita(self):
        produto = self.produto()
        ficha = self.post("fichas-tecnicas", {"produto": produto["id"], "receita": RECEITA, "observacoes": OBS,
                                              "itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "1"}]})
        self.assertEqual(ficha["receita"], RECEITA)
        self.tirar_permissao()
        self.assertNotIn("receita", self.ok(self.api_a.get(f"{V1}/fichas-tecnicas/{ficha['id']}/"))["data"])

    # --- exportação individual --------------------------------------------------------------
    def baixar(self, caminho, api=None, **params):
        resposta = (api or self.api_a).get(f"{V1}/{caminho}/exportar/", params)
        self.assertEqual(resposta.status_code, 200, resposta.content[:300])
        return resposta

    def textos_excel(self, resposta):
        planilha = openpyxl.load_workbook(io.BytesIO(resposta.content)).active
        return [str(c.value) for linha in planilha.iter_rows() for c in linha if c.value is not None]

    def test_exportar_excel_respeita_a_escolha_de_incluir(self):
        p = self.processado(receita=RECEITA, observacoes=OBS)
        sem = self.textos_excel(self.baixar(f"processados/{p['id']}", formato="xlsx"))
        com = self.textos_excel(self.baixar(f"processados/{p['id']}", formato="xlsx", incluir_receita="true"))
        self.assertFalse(any("Bata a farinha" in t for t in sem))
        self.assertTrue(any("Bata a farinha" in t for t in com))
        self.assertTrue(any("sem HTML" in t for t in com))

    def test_exportar_sem_permissao_ignora_o_pedido_de_incluir(self):
        p = self.processado(receita=RECEITA)
        self.tirar_permissao()
        texto = self.textos_excel(self.baixar(f"processados/{p['id']}", formato="xlsx", incluir_receita="true"))
        self.assertFalse(any("Bata a farinha" in t for t in texto))

    def test_exportar_pdf_e_formato_invalido(self):
        p = self.processado(receita=RECEITA)
        resposta = self.baixar(f"processados/{p['id']}", formato="pdf", incluir_receita="1")
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertTrue(resposta.content.startswith(b"%PDF"))
        self.erro(self.api_a.get(f"{V1}/processados/{p['id']}/exportar/", {"formato": "exe"}), 400)

    def test_exportar_ficha(self):
        produto = self.produto()
        ficha = self.post("fichas-tecnicas", {"produto": produto["id"], "receita": RECEITA,
                                              "itens": [{"insumo": self.farinha_a.pk, "medida": medida("kg").pk, "quantidade": "1"}]})
        self.assertTrue(self.baixar(f"fichas-tecnicas/{ficha['id']}", formato="pdf").content.startswith(b"%PDF"))

    def test_exportacao_e_auditada(self):
        p = self.processado(receita=RECEITA)
        self.baixar(f"processados/{p['id']}", formato="pdf", incluir_receita="true")
        registro = RegistroAuditoria.objects.filter(acao="exportar", modulo="processado").latest("id")
        self.assertEqual(registro.registro_id, str(p["id"]))
        self.assertIn("receita", registro.descricao)

    # --- tenant ----------------------------------------------------------------------------
    def test_outro_tenant_nao_exporta_nem_le(self):
        p = self.processado(receita=RECEITA)
        self.assertEqual(self.api_b.get(f"{V1}/processados/{p['id']}/exportar/", {"formato": "xlsx"}).status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/processados/{p['id']}/").status_code, 404)

    def test_exportar_exige_autenticacao(self):
        p = self.processado()
        self.assertEqual(self.anonimo.get(f"{V1}/processados/{p['id']}/exportar/").status_code, 401)


class ReceitaWebTests(CadastrosBase):
    """Telas: os campos só existem para quem tem a permissão; a exportação respeita o tenant."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario_a)
        self.p = Processado.objects.create(
            cliente=self.cliente_a, nome="Massa", rendimento_medida=medida("kg"), receita=RECEITA, observacoes=OBS
        )

    def test_formulario_mostra_receita_so_com_permissao(self):
        url = reverse("processado-edit", args=[self.p.pk])
        self.assertContains(self.client.get(url), 'name="receita"')
        self.usuario_a.groups.first().permissions.remove(*Permission.objects.filter(codename="ver_receita"))
        self.client.force_login(self.usuario_a)
        self.assertNotContains(self.client.get(url), 'name="receita"')

    def test_post_forjado_sem_permissao_nao_altera_a_receita(self):
        self.usuario_a.groups.first().permissions.remove(*Permission.objects.filter(codename="ver_receita"))
        self.client.force_login(self.usuario_a)
        dados = {"nome": "Massa", "rendimento_quantidade": "1", "rendimento_medida": medida("kg").pk, "ativo": "on",
                 "receita": "HACK", "observacoes": "HACK",
                 "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
                 "itens-0-insumo": self.farinha_a.pk, "itens-0-medida": medida("kg").pk, "itens-0-quantidade": "1"}
        self.client.post(reverse("processado-edit", args=[self.p.pk]), dados)
        self.p.refresh_from_db()
        self.assertEqual(self.p.receita, RECEITA)

    def test_exportar_web_so_do_proprio_tenant(self):
        self.assertEqual(self.client.get(reverse("processado-exportar", args=[self.p.pk]), {"formato": "pdf"}).status_code, 200)
        self.client.force_login(self.usuario_b)
        self.assertEqual(self.client.get(reverse("processado-exportar", args=[self.p.pk]), {"formato": "pdf"}).status_code, 404)

    def test_lista_pergunta_sobre_receita_so_com_permissao_e_texto(self):
        pagina = self.client.get(reverse("processado")).content.decode()
        self.assertIn('data-pergunta="1"', pagina)
        self.usuario_a.groups.first().permissions.remove(*Permission.objects.filter(codename="ver_receita"))
        self.client.force_login(self.usuario_a)
        pagina = self.client.get(reverse("processado")).content.decode()
        self.assertNotIn('data-pergunta="1"', pagina)
