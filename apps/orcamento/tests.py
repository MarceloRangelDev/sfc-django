"""Orçamento: cálculo, validação das linhas, telas, situação, versão para imprimir/enviar (omitir itens sem alterar o salvo), PDF, e-mail,
WhatsApp, link público, permissões e isolamento entre unidades. Nada fala com rede real: e-mail em memória, WhatsApp pela fila da integração."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.integracao import services as integracao
from apps.integracao.models import Mensagem
from apps.produto.models import Produto
from web_project.regras import RegraNegocioError

from . import services as s
from .models import Orcamento, OrcamentoEnvio, OrcamentoItem, OrcamentoLink
from .pdf import gerar_pdf

D = Decimal
V1 = "/api/v1"
CHAVE = Fernet.generate_key().decode()


def usuario(nome, cliente, grupo):
    u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password="SenhaForte123!")
    u.profile.cliente = cliente
    u.profile.save()
    u.groups.add(Group.objects.get(name=grupo))
    return u


class Req:
    def __init__(self, user):
        self.user, self.META = user, {}

    def build_absolute_uri(self, caminho="/"):
        return "https://sfc.exemplo.com" + caminho


class OrcamentoBase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_acl", verbosity=0)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A Ltda", nome_fantasia="Loja A",
                                        endereco="Rua 1, 100", cidade="Recife", uf="PE", email_contato="contato@lojaa.local", whatsapp="+5581999990000")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B Ltda")
        self.gestor = usuario("gestor_a", self.a, "Gestor")
        self.operador = usuario("operador_a", self.a, "Operador")
        self.visualizador = usuario("visual_a", self.a, "Visualizador")
        self.admin = usuario("admin_a", self.a, "Administrador")
        self.gestor_b = usuario("gestor_b", self.b, "Gestor")
        self.admin_b = usuario("admin_b", self.b, "Administrador")
        cat_a, cat_b = Categoria.objects.create(cliente=self.a, nome="Geral"), Categoria.objects.create(cliente=self.b, nome="Geral")
        self.bolo = Produto.objects.create(cliente=self.a, nome="Bolo de cenoura", categoria=cat_a)
        self.entrega = Produto.objects.create(cliente=self.a, nome="Entrega expressa", categoria=cat_a, tipo="servico")
        self.alheio = Produto.objects.create(cliente=self.b, nome="Produto da B", categoria=cat_b)
        self.client.force_login(self.gestor)

    def linhas(self, *extra):
        return [{"produto": self.bolo.pk, "quantidade": "2", "preco_unitario": 5000, "desconto": 500}, {"produto": self.entrega.pk, "quantidade": "1", "preco_unitario": 3000}, *extra]

    def criar(self, cliente=None, user=None, desconto=0, **extra):
        cliente, user = cliente or self.a, user or self.gestor
        o = Orcamento(cliente=cliente, criado_por=user, destinatario_nome="Maria", destinatario_email="maria@cliente.local", destinatario_whatsapp="+5581988887777", desconto=desconto, **extra)
        s.salvar_orcamento(Req(user), o, s.validar_linhas(cliente, self.linhas()))
        return o

    def api(self, user):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=user)[0].key}")
        return api


class CalculoETotaisTests(OrcamentoBase):
    def test_linha_total_e_descontos(self):
        self.assertEqual(s.total_da_linha(D("2"), 5000, 500), (10000, 9500))
        self.assertEqual(s.total_da_linha(D("0.333"), 100, 0), (33, 33))  # arredonda meio centavo para cima, só no final
        with self.assertRaises(RegraNegocioError):
            s.total_da_linha(D("1"), 1000, 1001)

    def test_totais_recalculados_no_servidor(self):
        o = self.criar(desconto=1000)
        self.assertEqual((o.subtotal, o.desconto_itens, o.total), (13000, 500, 11500))
        self.assertEqual(o.numero, 1)
        o.desconto = 99999  # desconto geral maior que o total dos itens
        with self.assertRaises(RegraNegocioError):
            s.salvar_orcamento(Req(self.gestor), o, s.validar_linhas(self.a, self.linhas()))

    def test_validacao_das_linhas(self):
        invalidas = [
            [{"produto": self.alheio.pk, "quantidade": "1", "preco_unitario": 100}],  # produto de outra unidade
            [{"produto": self.bolo.pk, "quantidade": "0", "preco_unitario": 100}],
            [{"produto": self.bolo.pk, "quantidade": "abc", "preco_unitario": 100}],
            [{"produto": self.bolo.pk, "quantidade": "1"}],  # sem preço e sem precificação do PDV
            [{"descricao": "", "quantidade": "1", "preco_unitario": 100}],  # avulsa sem descrição
            [{"produto": self.bolo.pk, "quantidade": "1", "preco_unitario": -1}],
            [],
        ]
        for linhas in invalidas:
            with self.assertRaises(RegraNegocioError, msg=linhas):
                s.validar_linhas(self.a, linhas)
        avulsa = s.validar_linhas(self.a, [{"descricao": "Frete", "tipo": "servico", "quantidade": "1", "preco_unitario": 1500}])[0]
        self.assertEqual((avulsa.produto, avulsa.tipo, avulsa.total), (None, "servico", 1500))

    def test_preco_do_pdv_e_usado_quando_o_preco_nao_vem(self):
        with mock.patch.object(s, "preco_sugerido", return_value=4200):
            item = s.validar_linhas(self.a, [{"produto": self.bolo.pk, "quantidade": "2"}])[0]
        self.assertEqual((item.preco_unitario, item.total), (4200, 8400))


class TelasETenantTests(OrcamentoBase):
    def dados(self, **extra):
        base = {"destinatario_nome": "Maria", "destinatario_email": "maria@cliente.local", "destinatario_whatsapp": "+5581988887777", "desconto": "10,00", "observacoes": "Pagamento em 2x",
                "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
                "form-0-produto": self.bolo.pk, "form-0-quantidade": "2", "form-0-preco_unitario": "50,00", "form-0-desconto": "5,00",
                "form-1-produto": "", "form-1-descricao": "Frete avulso", "form-1-tipo": "servico", "form-1-quantidade": "1", "form-1-preco_unitario": "30,00"}
        return {**base, **extra}

    def test_criar_pela_tela_numera_por_unidade_e_audita(self):
        r = self.client.post(reverse("orcamento-create"), self.dados())
        self.assertEqual(r.status_code, 302, r.content[:300])
        o = Orcamento.objects.get()
        self.assertEqual((o.cliente_id, o.numero, o.criado_por_id, o.status), (self.a.pk, 1, self.gestor.pk, "rascunho"))
        self.assertEqual((o.subtotal, o.desconto_itens, o.desconto, o.total), (13000, 500, 1000, 11500))
        self.assertEqual(list(o.itens.values_list("descricao", "tipo")), [("Bolo de cenoura", "produto"), ("Frete avulso", "servico")])
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="orcamento", acao="criar", tabela="orcamento_orcamento", registro_id=str(o.pk)).exists())
        self.client.force_login(self.gestor_b)  # a numeração é POR unidade: o primeiro da B também é o nº 1
        b = {k: v for k, v in self.dados().items() if not k.startswith("form-0")}
        b.update({"form-TOTAL_FORMS": "1", "form-0-produto": self.alheio.pk, "form-0-quantidade": "1", "form-0-preco_unitario": "10,00"})
        b = {k: v for k, v in b.items() if not k.startswith("form-1")}
        self.assertEqual(self.client.post(reverse("orcamento-create"), b).status_code, 302)
        self.assertEqual(Orcamento.objects.get(cliente=self.b).numero, 1)

    def test_total_enviado_pelo_cliente_e_ignorado_e_produto_alheio_recusado(self):
        self.client.post(reverse("orcamento-create"), self.dados(total="1", subtotal="1"))
        self.assertEqual(Orcamento.objects.get().total, 11500)
        r = self.client.post(reverse("orcamento-create"), self.dados(**{"form-0-produto": self.alheio.pk}))
        self.assertEqual(r.status_code, 200)  # produto de outra unidade: o campo recusa
        self.assertEqual(Orcamento.objects.count(), 1)

    def test_editar_e_fechado_nao_edita(self):
        o = self.criar()
        r = self.client.post(reverse("orcamento-edit", args=[o.pk]), self.dados(**{"form-1-preco_unitario": "40,00", "form-INITIAL_FORMS": "0"}))
        self.assertEqual(r.status_code, 302)
        o.refresh_from_db()
        self.assertEqual(o.total, 12500)
        s.mudar_situacao(Req(self.gestor), o, Orcamento.APROVADO)
        r = self.client.post(reverse("orcamento-edit", args=[o.pk]), self.dados())
        self.assertEqual(r.status_code, 302)  # volta ao detalhe com aviso; nada muda
        o.refresh_from_db()
        self.assertEqual(o.total, 12500)

    def test_paginas_renderizam_com_os_dados(self):
        o = self.criar()
        self.assertContains(self.client.get(reverse("orcamento-list")), "Maria")
        detalhe = self.client.get(reverse("orcamento-detalhe", args=[o.pk]))
        self.assertContains(detalhe, "Bolo de cenoura")
        self.assertContains(detalhe, "btn-enviar-orcamento")
        editar = self.client.get(reverse("orcamento-edit", args=[o.pk]))
        self.assertContains(editar, "Bolo de cenoura")
        self.assertContains(editar, "orcamento-form.js")
        enviar = self.client.get(reverse("orcamento-enviar", args=[o.pk]))
        self.assertContains(enviar, "orcamento-versao-itens")
        self.assertContains(enviar, 'name="incluir"')
        self.assertContains(self.client.get(reverse("orcamento-create")), "Novo orçamento")

    def test_outra_unidade_recebe_404_em_tudo(self):
        o = self.criar()
        self.client.force_login(self.admin_b)  # com todas as permissões: o que bloqueia é a unidade
        for nome, args in (("orcamento-detalhe", [o.pk]), ("orcamento-edit", [o.pk]), ("orcamento-pdf", [o.pk]), ("orcamento-enviar", [o.pk])):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 404, nome)
        for acao in ("aprovar", "cancelar", "excluir"):
            self.assertEqual(self.client.post(reverse("orcamento-acao", args=[o.pk, acao])).status_code, 404, acao)
        self.assertEqual(self.client.post(reverse("orcamento-enviar", args=[o.pk]), {"acao": "pdf"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("orcamento-list")), "Maria")

    def test_permissoes_por_perfil(self):
        o = self.criar()
        self.client.force_login(self.visualizador)  # só vê
        self.assertEqual(self.client.get(reverse("orcamento-detalhe", args=[o.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("orcamento-create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("orcamento-edit", args=[o.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("orcamento-acao", args=[o.pk, "aprovar"])).status_code, 403)
        self.client.force_login(self.operador)  # cria, mas não altera nem exclui
        self.assertEqual(self.client.get(reverse("orcamento-create")).status_code, 200)
        self.assertEqual(self.client.post(reverse("orcamento-acao", args=[o.pk, "excluir"])).status_code, 403)

    def test_situacao_e_exclusao(self):
        o = self.criar()
        self.client.post(reverse("orcamento-acao", args=[o.pk, "aprovar"]))
        o.refresh_from_db()
        self.assertEqual(o.status, "aprovado")
        self.client.post(reverse("orcamento-acao", args=[o.pk, "cancelar"]))  # fechado: não muda
        o.refresh_from_db()
        self.assertEqual(o.status, "aprovado")
        rascunho = self.criar()
        self.client.force_login(self.admin)
        self.client.post(reverse("orcamento-acao", args=[rascunho.pk, "excluir"]))
        self.assertFalse(Orcamento.objects.filter(pk=rascunho.pk).exists())
        enviado = self.criar()
        s.registrar_envio(Req(self.gestor), enviado, OrcamentoEnvio.EMAIL, OrcamentoEnvio.OK)
        self.client.post(reverse("orcamento-acao", args=[enviado.pk, "excluir"]))
        self.assertTrue(Orcamento.objects.filter(pk=enviado.pk).exists())  # já enviado: só cancela

    def test_previa_de_totais_vem_do_servidor(self):
        corpo = {"itens": [{"produto": self.bolo.pk, "quantidade": "2", "preco_unitario": "50,00", "desconto": "5,00"}, {"produto": self.alheio.pk, "quantidade": "1", "preco_unitario": "9,00"}], "desconto": "10,00"}
        r = self.client.post(reverse("orcamento-calcular"), corpo, content_type="application/json").json()
        self.assertEqual((r["itens"][0]["total"], r["total"]), ("R$ 95,00", "R$ 94,00"))  # (100 + 9) - 5 - 10
        self.assertEqual(r["itens"][1]["total"], "R$ 9,00")  # (a prévia não distingue unidade; a gravação recusa produto alheio)


class VersaoParaEnvioTests(OrcamentoBase):
    def test_omitir_itens_vale_so_para_a_versao(self):
        o = self.criar(desconto=500)
        bolo, entrega = o.itens.order_by("ordem")
        versao = s.versao_para_envio(o, [bolo.pk, 999999])  # id inexistente/alheio é ignorado
        self.assertEqual(([i.pk for i in versao.itens], versao.omitidos, versao.totais.total), ([entrega.pk], [bolo.pk], 3000 - 500))
        o.refresh_from_db()
        self.assertEqual(o.itens.count(), 2)  # o orçamento salvo continua com os dois itens
        with self.assertRaises(RegraNegocioError):  # não dá para omitir tudo
            s.versao_para_envio(o, [bolo.pk, entrega.pk])

    def test_desconto_geral_maior_que_a_versao_e_recusado(self):
        o = self.criar(desconto=5000)
        bolo, entrega = o.itens.order_by("ordem")
        with self.assertRaises(RegraNegocioError):
            s.versao_para_envio(o, [bolo.pk])  # restaria 30,00 de itens para 50,00 de desconto geral

    def test_persistir_exige_confirmacao_e_altera_o_orcamento_salvo(self):
        o = self.criar()
        bolo, entrega = o.itens.order_by("ordem")
        url = reverse("orcamento-enviar", args=[o.pk])
        r = self.client.post(url, {"acao": "pdf", "incluir": [entrega.pk], "persistir": "on"})  # sem a confirmação explícita
        self.assertEqual(r.status_code, 200)
        self.assertEqual(o.itens.count(), 2)
        r = self.client.post(url, {"acao": "pdf", "incluir": [entrega.pk], "persistir": "on", "confirmar_persistir": "on"})
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        o.refresh_from_db()
        self.assertEqual((o.itens.count(), o.subtotal, o.total), (1, 3000, 3000))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="orcamento", acao="editar", registro_id=str(o.pk)).exists())

    def test_so_quem_altera_pode_persistir(self):
        o = self.criar()
        bolo, entrega = o.itens.order_by("ordem")
        self.client.force_login(self.visualizador)
        r = self.client.post(reverse("orcamento-enviar", args=[o.pk]), {"acao": "pdf", "incluir": [entrega.pk], "persistir": "on", "confirmar_persistir": "on"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(o.itens.count(), 2)


class PdfTests(OrcamentoBase):
    def texto_do_pdf(self, versao):
        with mock.patch("reportlab.rl_config.pageCompression", 0):
            return gerar_pdf(versao, self.gestor)

    def test_pdf_tem_dados_da_empresa_data_hora_rodape_e_so_a_versao(self):
        o = self.criar(desconto=500)
        bolo, entrega = o.itens.order_by("ordem")
        pdf = self.texto_do_pdf(s.versao_para_envio(o, [bolo.pk]))
        self.assertTrue(pdf.startswith(b"%PDF"))
        for trecho in (b"Faturando Certo", b"Loja A", b"Entrega expressa", b"Emitido em", b"contato@lojaa.local", b"Recife", b"11.222.333/0001-81"):
            self.assertIn(trecho, pdf, trecho)
        self.assertNotIn(b"Bolo de cenoura", pdf)  # item omitido não aparece
        self.assertIn(b"Maria", pdf)

    def test_pdf_longo_pagina_e_logo_ausente_nao_quebra(self):
        o = self.criar()
        itens = s.validar_linhas(self.a, [{"descricao": f"Item {i:03d}", "quantidade": "1", "preco_unitario": 100 + i} for i in range(120)])
        s.salvar_orcamento(Req(self.gestor), o, itens, foto_antes=None)
        pdf = self.texto_do_pdf(s.versao_para_envio(o))
        self.assertGreaterEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 2)
        self.assertIn(b"Item 119", pdf)

    def test_texto_do_usuario_e_escapado(self):
        o = Orcamento(cliente=self.a, criado_por=self.gestor, destinatario_nome="<b>Fulano</b> & Cia", observacoes="<script>x</script>")
        s.salvar_orcamento(Req(self.gestor), o, s.validar_linhas(self.a, [{"descricao": "A <i>B</i>", "quantidade": "1", "preco_unitario": 100}]))
        self.assertTrue(self.texto_do_pdf(s.versao_para_envio(o)).startswith(b"%PDF"))  # nada de marcação interpretada: não levanta erro

    def test_view_do_pdf_e_download(self):
        o = self.criar()
        r = self.client.get(reverse("orcamento-pdf", args=[o.pk]))
        self.assertEqual((r.status_code, r["Content-Type"], r["Cache-Control"]), (200, "application/pdf", "private, no-store"))
        self.assertIn("inline", r["Content-Disposition"])
        self.assertIn("attachment", self.client.get(reverse("orcamento-pdf", args=[o.pk]), {"baixar": "1"})["Content-Disposition"])
        self.assertTrue(o.envios.filter(canal="pdf", status="ok").exists())

    def test_falha_ao_gerar_nao_perde_o_orcamento(self):
        o = self.criar()
        with mock.patch("apps.orcamento.views.gerar_pdf", side_effect=RuntimeError("boom")):
            r = self.client.get(reverse("orcamento-pdf", args=[o.pk]))
        self.assertEqual(r.status_code, 302)
        o.refresh_from_db()
        self.assertEqual((o.status, o.itens.count()), ("rascunho", 2))
        self.assertTrue(o.envios.filter(canal="pdf", status="falha").exists())


class EnvioPorEmailTests(OrcamentoBase):
    def enviar(self, o, **kw):
        return s.enviar_por_email(Req(self.gestor), o, **kw)

    def test_envia_pdf_anexo_marca_enviado_e_registra_sem_expor_destino(self):
        o = self.criar()
        bolo, _ = o.itens.order_by("ordem")
        self.enviar(o, omitir_ids=[bolo.pk])
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual((msg.to, msg.attachments[0][0], msg.attachments[0][2]), (["maria@cliente.local"], f"orcamento-{o.numero}.pdf", "application/pdf"))
        self.assertTrue(msg.attachments[0][1].startswith(b"%PDF"))
        self.assertEqual(msg.reply_to, ["contato@lojaa.local"])
        o.refresh_from_db()
        self.assertEqual((o.status, o.itens.count()), ("enviado", 2))  # o salvo não mudou
        envio = o.envios.get(canal="email")
        self.assertEqual((envio.status, envio.itens_omitidos), ("ok", [bolo.pk]))
        self.assertNotIn("maria@cliente.local", envio.destino)

    def test_falhas_nao_alteram_o_orcamento(self):
        o = self.criar()
        with self.assertRaises(RegraNegocioError):
            self.enviar(o, destino="invalido")
        with mock.patch("apps.orcamento.services.EmailMessage.send", side_effect=RuntimeError("smtp caiu: senha=segredo")):
            with self.assertRaises(RegraNegocioError) as ctx:
                self.enviar(o)
        self.assertNotIn("segredo", str(ctx.exception))
        o.refresh_from_db()
        self.assertEqual(o.status, "rascunho")
        self.assertEqual(o.envios.filter(canal="email", status="falha").count(), 1)
        self.assertNotIn("segredo", o.envios.get(status="falha").detalhe)

    def test_sem_configuracao_ou_sem_permissao(self):
        o = self.criar()
        with override_settings(EMAIL_BACKEND="config.email_backend.ResendEmailBackend", RESEND_API_KEY="", MAIL_FROM_EMAIL=""):
            with self.assertRaises(RegraNegocioError):
                self.enviar(o)
            self.assertFalse(s.canais_disponiveis(self.gestor, o)["email"])
        with self.assertRaises(RegraNegocioError):
            s.enviar_por_email(Req(self.operador), o)  # Operador não tem enviar_orcamento
        self.assertFalse(s.canais_disponiveis(self.operador, o)["email"])

    def test_cancelado_nao_envia(self):
        o = self.criar()
        s.mudar_situacao(Req(self.gestor), o, Orcamento.CANCELADO)
        with self.assertRaises(RegraNegocioError):
            self.enviar(o)

    def test_pela_tela(self):
        o = self.criar()
        bolo, entrega = o.itens.order_by("ordem")
        r = self.client.post(reverse("orcamento-enviar", args=[o.pk]), {"acao": "email", "incluir": [entrega.pk], "email": "outro@cliente.local"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(mail.outbox[0].to, ["outro@cliente.local"])


@override_settings(INTEGRACAO_CRYPTO_KEYS=CHAVE, BASE_URL="https://sfc.exemplo.com")
class EnvioPorWhatsAppTests(OrcamentoBase):
    def setUp(self):
        super().setUp()
        self.conexao = integracao.conectar(Req(self.gestor), self.a, "whatsapp_evolution", "Zap A", {"apikey": "k", "segredo_webhook": "s"},
                                           {"base_url": "https://evo.exemplo.com", "instancia": "loja-a"}, testar=False)
        self.conexao_b = integracao.conectar(Req(self.gestor_b), self.b, "whatsapp_evolution", "Zap B", {"apikey": "k", "segredo_webhook": "s"},
                                             {"base_url": "https://evo.exemplo.com", "instancia": "loja-b"}, testar=False)

    def test_enfileira_mensagem_com_link_do_pdf_da_versao(self):
        o = self.criar()
        bolo, entrega = o.itens.order_by("ordem")
        s.enviar_por_whatsapp(Req(self.gestor), o, self.conexao.pk, [bolo.pk])
        mensagem = Mensagem.objects.get(conexao=self.conexao, direcao="saida")
        link = OrcamentoLink.objects.get(orcamento=o)
        self.assertIn(f"https://sfc.exemplo.com/orcamento/publico/{link.token}", mensagem.texto)
        self.assertEqual((mensagem.status, link.itens_omitidos), ("pendente", [bolo.pk]))
        o.refresh_from_db()
        self.assertEqual(o.status, "enviado")
        self.assertTrue(o.envios.filter(canal="whatsapp", status="ok").exists())

    def test_conexao_de_outra_unidade_ou_sem_permissao_e_recusada(self):
        o = self.criar()
        with self.assertRaises(RegraNegocioError):
            s.enviar_por_whatsapp(Req(self.gestor), o, self.conexao_b.pk)  # conexão da B
        with self.assertRaises(RegraNegocioError):
            s.enviar_por_whatsapp(Req(self.operador), o, self.conexao.pk)  # Operador sem enviar_orcamento
        self.assertFalse(Mensagem.objects.exists())
        self.assertEqual([c.pk for c in s.canais_disponiveis(self.gestor, o)["whatsapp"]], [self.conexao.pk])
        self.assertEqual(s.canais_disponiveis(self.operador, o)["whatsapp"], [])

    def test_sem_contato_ou_conexao_inativa(self):
        o = self.criar()
        o.destinatario_whatsapp = ""
        o.save()
        with self.assertRaises(RegraNegocioError):
            s.enviar_por_whatsapp(Req(self.gestor), o, self.conexao.pk)
        o.destinatario_whatsapp = "+5581988887777"
        o.save()
        integracao.desconectar(Req(self.gestor), self.conexao, revogar=False)
        with self.assertRaises(RegraNegocioError):
            s.enviar_por_whatsapp(Req(self.gestor), o, self.conexao.pk)
        self.assertEqual(o.envios.filter(status="ok").count(), 0)


class LinkPublicoTests(OrcamentoBase):
    def setUp(self):
        super().setUp()
        self.o = self.criar()
        self.bolo_item, self.entrega_item = self.o.itens.order_by("ordem")

    def link(self, **kw):
        return s.criar_link(self.o, kw.pop("omitir", []))

    def test_baixa_sem_login_so_com_token_valido(self):
        self.client.logout()
        link = self.link(omitir=[self.bolo_item.pk])
        with mock.patch("reportlab.rl_config.pageCompression", 0):
            r = self.client.get(reverse("orcamento-publico", args=[link.token]))
        self.assertEqual((r.status_code, r["Content-Type"], r["X-Robots-Tag"]), (200, "application/pdf", "noindex, noarchive"))
        self.assertNotIn(b"Bolo de cenoura", r.content)
        self.assertEqual(self.client.get(reverse("orcamento-publico", args=["token-invalido"])).status_code, 404)

    def test_expira_e_morre_quando_o_orcamento_muda_ou_e_cancelado(self):
        self.client.logout()
        link = self.link()
        OrcamentoLink.objects.filter(pk=link.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get(reverse("orcamento-publico", args=[link.token])).status_code, 404)
        vivo = self.link()
        s.salvar_orcamento(Req(self.gestor), self.o, s.validar_linhas(self.a, self.linhas()), foto_antes=None)  # editar apaga os links
        self.assertEqual(self.client.get(reverse("orcamento-publico", args=[vivo.token])).status_code, 404)
        outro = self.link()
        s.mudar_situacao(Req(self.gestor), self.o, Orcamento.CANCELADO)
        self.assertEqual(self.client.get(reverse("orcamento-publico", args=[outro.token])).status_code, 404)

    def test_limite_de_downloads_por_ip(self):
        self.client.logout()
        link = self.link()
        with mock.patch("apps.orcamento.views.LIMITE_LINK_PUBLICO", (3, 60)):
            codigos = [self.client.get(reverse("orcamento-publico", args=[link.token])).status_code for _ in range(5)]
        self.assertEqual(codigos[:3], [200, 200, 200])
        self.assertIn(429, codigos[3:])


class ApiOrcamentoTests(OrcamentoBase):
    def corpo(self, **extra):
        return {"destinatario_nome": "João", "destinatario_email": "joao@cliente.local", "desconto": 500,
                "itens": [{"produto": self.bolo.pk, "quantidade": "2", "preco_unitario": 5000, "desconto": 500}, {"descricao": "Frete", "tipo": "servico", "quantidade": "1", "preco_unitario": 2000}], **extra}

    def test_crud_completo_com_totais_do_servidor(self):
        api = self.api(self.gestor)
        r = api.post(f"{V1}/orcamentos/", self.corpo(total=1), format="json")
        self.assertEqual(r.status_code, 201, r.content[:400])
        dado = r.json()["data"]
        self.assertEqual((dado["numero"], dado["subtotal"], dado["desconto_itens"], dado["total"], dado["status"]), (1, 12000, 500, 11000, "rascunho"))
        pk = dado["id"]
        self.assertEqual(len(api.get(f"{V1}/orcamentos/?situacao=rascunho").json()["data"]["itens"]), 1)
        r = api.patch(f"{V1}/orcamentos/{pk}/", {"observacoes": "Combinado"}, format="json")  # PATCH mantém os itens
        self.assertEqual((r.json()["data"]["observacoes"], r.json()["data"]["total"], len(r.json()["data"]["itens"])), ("Combinado", 11000, 2))
        r = api.patch(f"{V1}/orcamentos/{pk}/", {"itens": [{"descricao": "Só um", "quantidade": "1", "preco_unitario": 1000}], "desconto": 0}, format="json")
        self.assertEqual(r.json()["data"]["total"], 1000)
        self.assertEqual(api.post(f"{V1}/orcamentos/{pk}/aprovar/").json()["data"]["status"], "aprovado")
        self.assertEqual(api.patch(f"{V1}/orcamentos/{pk}/", {"observacoes": "x"}, format="json").status_code, 422)  # fechado
        self.assertEqual(api.delete(f"{V1}/orcamentos/{pk}/").status_code, 403)  # Gestor não exclui

    def test_isolamento_e_permissoes(self):
        o = self.criar()
        outro = self.api(self.gestor_b)
        self.assertEqual(outro.get(f"{V1}/orcamentos/").json()["data"]["itens"], [])
        for chamada in (outro.get(f"{V1}/orcamentos/{o.pk}/"), outro.patch(f"{V1}/orcamentos/{o.pk}/", {"observacoes": "x"}, format="json"), outro.post(f"{V1}/orcamentos/{o.pk}/aprovar/"),
                        outro.get(f"{V1}/orcamentos/{o.pk}/pdf/"), outro.post(f"{V1}/orcamentos/{o.pk}/enviar/", {"canal": "email"}, format="json"), outro.get(f"{V1}/orcamentos/{o.pk}/canais/")):
            self.assertEqual(chamada.status_code, 404)
        r = outro.post(f"{V1}/orcamentos/", {**self.corpo(), "itens": [{"produto": self.bolo.pk, "quantidade": "1", "preco_unitario": 100}]}, format="json")
        self.assertEqual(r.status_code, 422)  # produto da A usado pela B
        self.assertEqual(self.api(self.visualizador).post(f"{V1}/orcamentos/", self.corpo(), format="json").status_code, 403)
        self.assertEqual(self.api(self.admin).delete(f"{V1}/orcamentos/{o.pk}/").status_code, 204)

    def test_pdf_canais_e_envio(self):
        o = self.criar()
        api = self.api(self.gestor)
        r = api.get(f"{V1}/orcamentos/{o.pk}/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        canais = api.get(f"{V1}/orcamentos/{o.pk}/canais/").json()["data"]
        self.assertEqual((canais["email"], canais["whatsapp"]), (True, []))
        bolo, entrega = o.itens.order_by("ordem")
        r = api.post(f"{V1}/orcamentos/{o.pk}/enviar/", {"canal": "email", "omitir_itens": [bolo.pk]}, format="json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(r.json()["data"]["status"], "enviado")
        # alteração permanente só com confirmação explícita
        r = api.post(f"{V1}/orcamentos/{o.pk}/enviar/", {"canal": "email", "omitir_itens": [bolo.pk], "persistir_alteracao": True}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(o.itens.count(), 2)
        r = api.post(f"{V1}/orcamentos/{o.pk}/enviar/", {"canal": "email", "omitir_itens": [bolo.pk], "persistir_alteracao": True, "confirmar_persistencia": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(o.itens.count(), 1)
        self.assertEqual(api.post(f"{V1}/orcamentos/{o.pk}/enviar/", {"canal": "sms"}, format="json").status_code, 422)


class ResendComAnexoTests(TestCase):
    """O backend de e-mail do Resend passou a repassar anexos (o PDF do orçamento) em Base64."""

    @override_settings(RESEND_API_KEY="chave-de-teste")
    def test_anexo_vai_em_base64_e_mensagem_sem_anexo_continua_igual(self):
        import base64
        import sys
        from django.core.mail import EmailMessage

        from config.email_backend import ResendEmailBackend

        falso = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"resend": falso}):
            com = EmailMessage("Assunto", "Corpo", "De <de@x.local>", ["para@x.local"])
            com.attach("orcamento-1.pdf", b"%PDF-1.4 teste", "application/pdf")
            sem = EmailMessage("Outro", "Corpo", "De <de@x.local>", ["para@x.local"])
            self.assertEqual(ResendEmailBackend().send_messages([com, sem]), 2)
        enviados = [c.args[0] for c in falso.Emails.send.call_args_list]
        self.assertEqual(enviados[0]["attachments"], [{"filename": "orcamento-1.pdf", "content": base64.b64encode(b"%PDF-1.4 teste").decode()}])
        self.assertNotIn("attachments", enviados[1])
