"""Templates do WhatsApp (Meta): validação do conteúdo, troca com a Meta (SEMPRE simulada), isolamento entre unidades e permissões."""
import json
from unittest import mock

from django.urls import reverse
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from web_project.regras import RegraNegocioError

from . import rede, services as s, templates_whatsapp as tw
from .models import TemplateWhatsApp
from .tests import Base, V1, Token

CRED = {"access_token": "token-secreto-meta", "app_secret": "seg", "verify_token": "vt"}
CFG = {"phone_number_id": "111", "waba_id": "99887766"}
CONTEUDO = {"nome": "pedido_confirmado", "idioma": "pt_BR", "categoria": "UTILITY", "cabecalho": "", "corpo": "Olá {{1}}, seu pedido {{2}} foi confirmado!", "rodape": "Obrigado",
            "botoes": [], "exemplos": {"cabecalho": "", "corpo": ["Maria", "1234"]}}


def graph(respostas):
    """Simula rede.requisitar: `respostas` = lista de dados devolvidos em ordem. Registra as chamadas em .calls."""
    return mock.patch.object(rede, "requisitar", side_effect=[(200, {}, r) for r in respostas])


class TemplatesBase(Base):
    def setUp(self):
        super().setUp()
        self.wa = s.conectar(self.req, self.cliente, "whatsapp_cloud", "Meta A", CRED, CFG, testar=False)
        self.wa_b = s.conectar(type(self.req)(self.gestor_b), self.outro, "whatsapp_cloud", "Meta B", CRED, CFG, testar=False)

    def rascunho(self, conexao=None, **extra):
        return tw.criar_local(self.req, conexao or self.wa, {**CONTEUDO, **extra})


class ValidacaoTests(TemplatesBase):
    def erro(self, **extra):
        with self.assertRaises(RegraNegocioError) as ctx:
            tw.validar_dados({**CONTEUDO, **extra})
        return str(ctx.exception)

    def test_conteudo_valido_gera_componentes_com_exemplos(self):
        t = self.rascunho(botoes=[{"tipo": "URL", "texto": "Ver", "url": "https://x.com.br/p/{{1}}", "exemplo": "abc"}, {"tipo": "QUICK_REPLY", "texto": "Ok"}])
        comp = tw.montar_componentes(t)
        self.assertEqual([c["type"] for c in comp], ["BODY", "FOOTER", "BUTTONS"])
        self.assertEqual(comp[0]["example"], {"body_text": [["Maria", "1234"]]})
        self.assertEqual([b["type"] for b in comp[2]["buttons"]], ["QUICK_REPLY", "URL"])  # resposta rápida sempre antes

    def test_regras_do_nome_idioma_categoria(self):
        self.assertIn("Nome", self.erro(nome="Pedido Confirmado"))
        self.assertIn("Idioma", self.erro(idioma="portugues"))
        self.assertIn("Categoria", self.erro(categoria="AUTHENTICATION"))

    def test_regras_das_variaveis(self):
        self.assertIn("sequência", self.erro(corpo="a {{2}} b", exemplos={"corpo": ["x"]}))
        self.assertIn("início nem no fim", self.erro(corpo="{{1}} chegou", exemplos={"corpo": ["x"]}))
        self.assertIn("exemplo", self.erro(exemplos={"corpo": ["Maria"]}))
        self.assertIn("variáveis", self.erro(rodape="oi {{1}}"))
        self.assertIn("1024", self.erro(corpo="x" * 1025))

    def test_regras_dos_botoes(self):
        self.assertIn("https", self.erro(botoes=[{"tipo": "URL", "texto": "a", "url": "http://x.com"}]))
        self.assertIn("telefone", self.erro(botoes=[{"tipo": "PHONE_NUMBER", "texto": "a", "telefone": "123"}]))
        self.assertIn("tipo inválido", self.erro(botoes=[{"tipo": "FLOW", "texto": "a"}]))
        self.assertIn("No máximo 10", self.erro(botoes=[{"tipo": "QUICK_REPLY", "texto": "a"}] * 11))


class OperacoesTests(TemplatesBase):
    def test_criar_e_editar_sao_locais_e_nao_chamam_a_meta(self):
        with mock.patch.object(rede, "requisitar") as req:
            t = self.rascunho()
            tw.editar_local(self.req, t, {**CONTEUDO, "rodape": "Valeu"})
        req.assert_not_called()
        t.refresh_from_db()
        self.assertEqual((t.situacao_local, t.status_meta, t.id_meta, t.rodape), ("rascunho", "", "", "Valeu"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="integracao", tabela="integracao_templatewhatsapp", acao="criar").exists())
        with self.assertRaises(RegraNegocioError):
            self.rascunho()  # mesmo nome + idioma

    def test_enviar_usa_token_waba_e_grava_o_status_que_a_meta_devolveu(self):
        t = self.rascunho()
        with graph([{"id": "tpl-1", "status": "PENDING", "category": "UTILITY"}]) as req:
            tw.enviar_para_meta(self.req, t)
        metodo, url, cab, corpo = req.call_args[0]
        self.assertEqual((metodo, url), ("POST", "https://graph.facebook.com/v21.0/99887766/message_templates"))
        self.assertEqual(cab["Authorization"], "Bearer token-secreto-meta")
        self.assertEqual((corpo["name"], corpo["language"], corpo["category"]), ("pedido_confirmado", "pt_BR", "UTILITY"))
        t.refresh_from_db()
        self.assertEqual((t.id_meta, t.status_meta, t.situacao_local), ("tpl-1", "PENDING", "sincronizado"))

    def test_falha_da_meta_nao_marca_nada_como_enviado_e_nao_vaza_o_token(self):
        t = self.rascunho()
        erro = rede.ErroDeRede("O provedor respondeu HTTP 400.", status=400, temporario=False, detalhe="Corpo inválido")
        with mock.patch.object(rede, "requisitar", side_effect=erro):
            with self.assertRaises(RegraNegocioError) as ctx:
                tw.enviar_para_meta(self.req, t)
        self.assertIn("Corpo inválido", str(ctx.exception))
        self.assertNotIn("token-secreto-meta", str(ctx.exception))
        t.refresh_from_db()
        self.assertEqual((t.id_meta, t.status_meta, t.situacao_local), ("", "", "rascunho"))
        self.assertIn("Corpo inválido", t.ultimo_erro)

    def test_conexao_sem_waba_ou_inativa_nao_envia(self):
        sem_waba = s.conectar(self.req, self.cliente, "whatsapp_cloud", "Sem WABA", CRED, {"phone_number_id": "1"}, testar=False)
        t = self.rascunho(sem_waba)
        with mock.patch.object(rede, "requisitar") as req, self.assertRaises(RegraNegocioError):
            tw.enviar_para_meta(self.req, t)
        req.assert_not_called()
        s.desconectar(self.req, self.wa, revogar=False)
        with self.assertRaises(RegraNegocioError):
            tw.enviar_para_meta(self.req, self.rascunho(nome="outro"))

    def test_so_a_conexao_whatsapp_oficial_tem_templates(self):
        teste = self.conexao()
        with self.assertRaises(RegraNegocioError):
            tw.criar_local(self.req, teste, CONTEUDO)

    def test_editar_enviado_trava_nome_e_reenvia_pela_meta_relendo_o_status(self):
        t = self.rascunho()
        with graph([{"id": "tpl-1", "status": "APPROVED", "category": "UTILITY"}]):
            tw.enviar_para_meta(self.req, t)
        with self.assertRaises(RegraNegocioError):
            tw.editar_local(self.req, t, {**CONTEUDO, "nome": "outro_nome"})
        tw.editar_local(self.req, t, {**CONTEUDO, "corpo": "Oi {{1}}, tudo certo com {{2}}!"})
        t.refresh_from_db()
        self.assertEqual(t.situacao_local, "alterado")
        with graph([{"success": True}, {"id": "tpl-1", "name": "pedido_confirmado", "status": "PENDING", "category": "UTILITY"}]) as req:
            tw.enviar_para_meta(self.req, t)
        self.assertEqual(req.call_args_list[0][0][:2], ("POST", "https://graph.facebook.com/v21.0/tpl-1"))
        t.refresh_from_db()
        self.assertEqual((t.status_meta, t.situacao_local), ("PENDING", "sincronizado"))
        with self.assertRaises(RegraNegocioError):  # PENDING: a Meta não aceita edição e nada a enviar
            tw.enviar_para_meta(self.req, t)

    def test_sincronizar_importa_atualiza_e_preserva_edicao_local_pendente(self):
        local = self.rascunho()
        with graph([{"id": "tpl-1", "status": "REJECTED", "category": "UTILITY"}]):
            tw.enviar_para_meta(self.req, local)
        tw.editar_local(self.req, local, {**CONTEUDO, "rodape": "Local"})  # alteração pendente
        lista = {"data": [
            {"id": "tpl-1", "name": "pedido_confirmado", "language": "pt_BR", "category": "UTILITY", "status": "APPROVED", "rejected_reason": "NONE",
             "components": [{"type": "BODY", "text": "da Meta"}]},
            {"id": "tpl-2", "name": "so_na_meta", "language": "en_US", "category": "MARKETING", "status": "APPROVED",
             "components": [{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Hello"}]},
            {"id": "tpl-3", "name": "otp", "language": "pt_BR", "category": "AUTHENTICATION", "status": "APPROVED", "components": [{"type": "BODY", "text": "x"}]},
        ]}
        with graph([lista]) as req:
            resumo = tw.sincronizar(self.req, self.wa)
        self.assertIn("/99887766/message_templates?limit=100", req.call_args[0][1])
        self.assertEqual(resumo, {"importados": 2, "atualizados": 1, "ausentes": 0})
        local.refresh_from_db()
        self.assertEqual((local.status_meta, local.rodape, local.situacao_local), ("APPROVED", "Local", "alterado"))  # status da Meta entra, edição local fica
        importado = TemplateWhatsApp.objects.get(nome="so_na_meta")
        self.assertTrue(importado.somente_leitura)  # cabeçalho de mídia não é editável aqui
        self.assertTrue(TemplateWhatsApp.objects.get(nome="otp").somente_leitura)
        with self.assertRaises(RegraNegocioError):
            tw.editar_local(self.req, importado, CONTEUDO)

    def test_excluir_enviado_apaga_na_meta_primeiro(self):
        t = self.rascunho()
        with graph([{"id": "tpl-1", "status": "PENDING"}]):
            tw.enviar_para_meta(self.req, t)
        with mock.patch.object(rede, "requisitar", side_effect=rede.ErroDeRede("HTTP 500", status=500)):
            with self.assertRaises(RegraNegocioError):
                tw.excluir(self.req, t)
        self.assertTrue(TemplateWhatsApp.objects.filter(pk=t.pk).exists())  # Meta falhou: mantém
        with graph([{"success": True}]) as req:
            tw.excluir(self.req, t)
        metodo, url = req.call_args[0][:2]
        self.assertEqual(metodo, "DELETE")
        self.assertIn("name=pedido_confirmado&hsm_id=tpl-1", url)
        self.assertFalse(TemplateWhatsApp.objects.filter(pk=t.pk).exists())

    def test_excluir_rascunho_nao_chama_a_meta(self):
        t = self.rascunho()
        with mock.patch.object(rede, "requisitar") as req:
            tw.excluir(self.req, t)
        req.assert_not_called()

    def test_conexao_com_templates_nao_pode_ser_excluida(self):
        self.rascunho()
        s.desconectar(self.req, self.wa, revogar=False)
        with self.assertRaises(RegraNegocioError):
            s.excluir_conexao(self.req, self.wa)


class TelasETenantTests(TemplatesBase):
    def setUp(self):
        super().setUp()
        self.t_a = self.rascunho()
        self.t_b = tw.criar_local(type(self.req)(self.gestor_b), self.wa_b, CONTEUDO)

    def test_lista_mostra_so_a_propria_unidade(self):
        self.client.force_login(self.gestor)
        pagina = self.client.get(reverse("template-whatsapp-list")).content.decode()
        self.assertIn("Meta A", pagina)
        self.assertNotIn("Meta B", pagina)
        self.assertEqual(self.client.get(reverse("template-whatsapp-list"), {"conexao": self.wa_b.pk}).content.decode().count("pedido_confirmado"), 0)

    def test_nao_acessa_nem_altera_template_de_outra_unidade(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("template-whatsapp-edit", args=[self.t_b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("template-whatsapp-acao", args=[self.t_b.pk, "enviar"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("template-whatsapp-sincronizar"), {"conexao": self.wa_b.pk}).status_code, 404)
        r = self.client.post(reverse("template-whatsapp-create"), {**{k: v for k, v in CONTEUDO.items() if k in ("nome", "idioma", "categoria", "corpo")}, "conexao": self.wa_b.pk, "nome": "invasor"})
        self.assertEqual(r.status_code, 200)  # conexão fora das opções do formulário: recusada
        self.assertFalse(TemplateWhatsApp.objects.filter(nome="invasor").exists())

    def test_criar_pela_tela_e_enviar(self):
        self.client.force_login(self.gestor)
        dados = {"conexao": self.wa.pk, "nome": "novo_modelo", "idioma": "pt_BR", "categoria": "MARKETING", "corpo": "Promoção para {{1}} hoje!", "exemplos_corpo": "Maria",
                 "botao1_tipo": "QUICK_REPLY", "botao1_texto": "Quero"}
        self.assertEqual(self.client.post(reverse("template-whatsapp-create"), dados).status_code, 302)
        novo = TemplateWhatsApp.objects.get(nome="novo_modelo")
        self.assertEqual((novo.conexao_id, novo.situacao_local, novo.botoes), (self.wa.pk, "rascunho", [{"tipo": "QUICK_REPLY", "texto": "Quero"}]))
        with graph([{"id": "t9", "status": "PENDING"}]):
            self.assertEqual(self.client.post(reverse("template-whatsapp-acao", args=[novo.pk, "enviar"])).status_code, 302)
        novo.refresh_from_db()
        self.assertEqual(novo.status_meta, "PENDING")

    def test_perfis_sem_permissao(self):
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("template-whatsapp-list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("template-whatsapp-acao", args=[self.t_a.pk, "enviar"])).status_code, 403)
        self.client.force_login(self.gestor)  # Gestor vê/cria/altera, mas excluir é do Administrador
        self.assertEqual(self.client.get(reverse("template-whatsapp-list")).status_code, 200)
        self.assertEqual(self.client.post(reverse("template-whatsapp-acao", args=[self.t_a.pk, "excluir"])).status_code, 403)
        self.assertTrue(TemplateWhatsApp.objects.filter(pk=self.t_a.pk).exists())


class APITemplatesTests(TemplatesBase):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o, self.api_b = (self.api(u) for u in (self.gestor, self.operador, self.gestor_b))

    def api(self, user):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=user)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def test_fluxo_completo_e_isolamento(self):
        r = self.api_g.post(f"{V1}/whatsapp-templates/", {**CONTEUDO, "conexao": self.wa.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        dado = r.json()["data"]
        self.assertEqual((dado["situacao_local"], dado["status_meta"]), ("rascunho", ""))
        self.assertNotIn("token-secreto-meta", json.dumps(dado))
        pk = dado["id"]
        self.assertEqual(self.api_g.patch(f"{V1}/whatsapp-templates/{pk}/", {"rodape": "Novo"}, format="json").json()["data"]["rodape"], "Novo")
        with graph([{"id": "t1", "status": "PENDING"}]):
            enviado = self.api_g.post(f"{V1}/whatsapp-templates/{pk}/enviar/").json()["data"]
        self.assertEqual((enviado["status_meta"], enviado["id_meta"]), ("PENDING", "t1"))
        self.assertEqual(len(self.api_g.get(f"{V1}/whatsapp-templates/?status=pending").json()["data"]["itens"]), 1)
        # outra unidade: nem lista, nem lê, nem altera, nem usa a conexão alheia
        self.assertEqual(self.api_b.get(f"{V1}/whatsapp-templates/").json()["data"]["itens"], [])
        for chamada in (self.api_b.get(f"{V1}/whatsapp-templates/{pk}/"), self.api_b.patch(f"{V1}/whatsapp-templates/{pk}/", {"corpo": "x"}, format="json"),
                        self.api_b.post(f"{V1}/whatsapp-templates/{pk}/enviar/")):
            self.assertEqual(chamada.status_code, 404)
        self.assertEqual(self.api_b.post(f"{V1}/whatsapp-templates/", {**CONTEUDO, "conexao": self.wa.pk}, format="json").status_code, 400)
        self.assertEqual(self.api_b.post(f"{V1}/whatsapp-templates/sincronizar/", {"conexao": self.wa.pk}, format="json").status_code, 400)

    def test_permissoes_e_validacao(self):
        self.assertEqual(self.api_o.get(f"{V1}/whatsapp-templates/").status_code, 403)
        r = self.api_g.post(f"{V1}/whatsapp-templates/", {**CONTEUDO, "conexao": self.wa.pk, "nome": "Inválido"}, format="json")
        self.assertEqual(r.status_code, 422, r.content[:300])
        t = self.rascunho()
        self.assertEqual(self.api_g.delete(f"{V1}/whatsapp-templates/{t.pk}/").status_code, 403)
