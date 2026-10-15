"""Fiscal: prontidão, certificado A1 (cifrado, validado de verdade), pedido de documento (idempotente, PENDENTE), fila de pendentes,
regra de ouro (nunca autorizar sem chave/protocolo reais), cancelamento, permissões, tenant, auditoria e API."""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.pdv.tests import PdvBase, Req
from web_project.regras import RegraNegocioError
from web_project.testing import habilitar_modulo

from . import services as s
from .models import ConfiguracaoFiscal, DocumentoFiscal
from .provedores import ErroFiscal, ProvedorFiscal, ResultadoEmissao

CHAVE = Fernet.generate_key().decode()
cripto = override_settings(INTEGRACAO_CRYPTO_KEYS=CHAVE)
V1 = "/api/v1"
SENHA_PFX = "senha-do-certificado-123"


def gerar_pfx(senha=SENHA_PFX, dias=365):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA TESTE LTDA:11222333000181")])
    agora = datetime.now(dt_timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=400)).not_valid_after(agora + timedelta(days=dias)).sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(b"teste", chave, cert, None, serialization.BestAvailableEncryption(senha.encode()))


class Falso(ProvedorFiscal):
    """Provedor de TESTE (nunca registrado no REGISTRO real): devolve o resultado programado."""

    chave = "falso"
    tipos = ("nfse", "nfe", "nfce")

    def __init__(self, resultado=None, erro=None):
        self.resultado, self.erro, self.chamadas = resultado, erro, 0

    def pronto(self, documento, configuracao):
        return True, ""

    def emitir(self, documento, configuracao, certificado):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.resultado

    def cancelar(self, documento, configuracao, certificado, motivo):
        if self.erro:
            raise self.erro


@cripto
class FiscalBase(PdvBase):
    def setUp(self):
        super().setUp()
        for c in (self.cliente, self.outro):
            habilitar_modulo(c, "fiscal")
            habilitar_modulo(c, "fiscal-config")
        self.cliente.emite_nota_fiscal, self.cliente.regime_tributario = True, "simples"
        self.cliente.save()
        self.config = ConfiguracaoFiscal.da_unidade(self.cliente)
        self.caixa = self.abrir()
        self.venda, _ = self.vender(self.caixa, qtd=2)

    def preparar(self, **extra):
        """Deixa o cadastro fiscal completo para NFS-e (menos o provedor real, que não emite)."""
        self.config.habilita_nfse, self.config.codigo_municipio, self.config.inscricao_municipal = True, "3550308", "123456"
        self.config.provedor = "nfse_nacional"
        for campo, valor in extra.items():
            setattr(self.config, campo, valor)
        self.config.save()
        with cripto:
            s.salvar_certificado(self.req_g, self.config, gerar_pfx(), SENHA_PFX)
        self.config.refresh_from_db()

    def pedir(self, tipo="nfse"):
        documento, _ = s.solicitar_emissao(self.req_g, self.venda, tipo)
        return documento


class CertificadoTests(FiscalBase):
    def test_guarda_cifrado_e_le_titular_e_validade(self):
        with cripto:
            s.salvar_certificado(self.req_g, self.config, gerar_pfx(), SENHA_PFX)
            self.config.refresh_from_db()
            self.assertTrue(self.config.tem_certificado)
            self.assertIn("EMPRESA TESTE LTDA", self.config.certificado_assunto)
            self.assertGreater(self.config.certificado_validade, timezone.now())
            self.assertNotIn(SENHA_PFX, self.config.certificado_cifrado)
            pfx, senha = s.certificado_de(self.config)
            self.assertEqual(senha, SENHA_PFX)
            self.assertTrue(pfx.startswith(b"0"))  # DER

    def test_senha_errada_vencido_e_lixo_sao_recusados(self):
        with cripto:
            with self.assertRaises(RegraNegocioError):
                s.salvar_certificado(self.req_g, self.config, gerar_pfx(), "errada")
            with self.assertRaises(RegraNegocioError):
                s.salvar_certificado(self.req_g, self.config, gerar_pfx(dias=-1), SENHA_PFX)
            with self.assertRaises(RegraNegocioError):
                s.salvar_certificado(self.req_g, self.config, b"nao e um pfx", SENHA_PFX)
            with self.assertRaises(RegraNegocioError):
                s.salvar_certificado(self.req_g, self.config, b"", SENHA_PFX)
        self.config.refresh_from_db()
        self.assertFalse(self.config.tem_certificado)

    def test_sem_chave_de_cifra_em_producao_recusa(self):
        with override_settings(INTEGRACAO_CRYPTO_KEYS="", DEBUG=False):
            with self.assertRaises(RegraNegocioError):
                s.salvar_certificado(self.req_g, self.config, gerar_pfx(), SENHA_PFX)

    def test_auditoria_nao_traz_arquivo_nem_senha(self):
        with cripto:
            s.salvar_certificado(self.req_g, self.config, gerar_pfx(), SENHA_PFX)
        for registro in RegistroAuditoria.objects.filter(modulo="fiscal"):
            texto = f"{registro.descricao} {registro.alteracoes}"
            self.assertNotIn(SENHA_PFX, texto)
            self.assertNotIn("certificado_cifrado", texto)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="fiscal", descricao__contains="Certificado").exists())

    def test_remover(self):
        with cripto:
            s.salvar_certificado(self.req_g, self.config, gerar_pfx(), SENHA_PFX)
        s.remover_certificado(self.req_g, self.config)
        self.config.refresh_from_db()
        self.assertEqual((self.config.certificado_cifrado, self.config.certificado_validade), ("", None))


class RequisitosTests(FiscalBase):
    def test_lista_o_que_falta(self):
        falta = " ".join(s.requisitos(self.cliente, "nfse"))
        for trecho in ("não está habilitado", "Certificado digital", "município", "Inscrição municipal", "provedor"):
            self.assertIn(trecho, falta)

    def test_empresa_que_nao_emite(self):
        self.cliente.emite_nota_fiscal = False
        self.cliente.save()
        self.assertEqual(len(s.requisitos(self.cliente, "nfse")), 1)

    def test_regime_obrigatorio(self):
        self.cliente.regime_tributario = ""
        self.cliente.save()
        self.assertIn("Regime tributário", " ".join(s.requisitos(self.cliente, "nfse")))

    def test_cadastro_completo_nao_tem_pendencia(self):
        self.preparar()
        self.assertEqual(s.requisitos(self.cliente, "nfse"), [])

    def test_nfe_exige_inscricao_estadual(self):
        self.config.habilita_nfe = True
        self.config.save()
        self.assertIn("Inscrição estadual", " ".join(s.requisitos(self.cliente, "nfe")))


class SolicitarTests(FiscalBase):
    def test_cria_pendente_com_motivo_e_nao_mexe_na_venda(self):
        documento = self.pedir()
        self.assertEqual((documento.status, documento.chave_acesso, documento.protocolo), ("pendente", "", ""))
        self.assertIn("Certificado", documento.motivo)
        self.venda.refresh_from_db()
        self.assertEqual(self.venda.status, "paga")

    def test_idempotente(self):
        d1, c1 = s.solicitar_emissao(self.req_g, self.venda, "nfse")
        d2, c2 = s.solicitar_emissao(self.req_g, self.venda, "nfse")
        self.assertEqual((d1.pk, c1, c2), (d2.pk, True, False))
        self.assertEqual(DocumentoFiscal.objects.count(), 1)

    def test_so_venda_paga_e_empresa_que_emite(self):
        from apps.pdvconfig.models import Mesa
        from apps.pdv import services as pdv

        venda_aberta = pdv.abrir_comanda(self.req_o, self.caixa, Mesa.objects.create(cliente=self.cliente, nome="M1"))
        with self.assertRaises(RegraNegocioError):
            s.solicitar_emissao(self.req_g, venda_aberta, "nfse")
        with self.assertRaises(RegraNegocioError):
            s.solicitar_emissao(self.req_g, self.venda, "xyz")
        self.cliente.emite_nota_fiscal = False
        self.cliente.save()
        self.venda.refresh_from_db()
        with self.assertRaises(RegraNegocioError):
            s.solicitar_emissao(self.req_g, self.venda, "nfse")

    def test_auditoria_da_criacao(self):
        documento = self.pedir()
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="fiscal", registro_id=str(documento.pk), acao="criar").exists())


class FilaTests(FiscalBase):
    def processar(self, provedor):
        with mock.patch("apps.fiscal.services.obter", return_value=provedor):
            return s.processar_pendentes()

    def test_provedor_real_nao_emite_nada(self):
        """NFS-e Nacional ainda não concluído: com tudo configurado o documento continua PENDENTE (nunca simula)."""
        self.preparar()
        documento = self.pedir()
        resumo = s.processar_pendentes()
        documento.refresh_from_db()
        self.assertEqual((resumo["autorizadas"], documento.status, documento.tentativas, documento.chave_acesso), (0, "pendente", 0, ""))
        self.assertIn("ainda não concluída", documento.motivo)

    def test_sem_configuracao_so_atualiza_o_motivo(self):
        documento = self.pedir()
        resumo = s.processar_pendentes()
        documento.refresh_from_db()
        self.assertEqual((resumo["aguardando"], documento.status, documento.tentativas), (1, "pendente", 0))

    def test_autorizada_so_com_chave_e_protocolo(self):
        self.preparar()
        documento = self.pedir()
        r = self.processar(Falso(ResultadoEmissao(autorizada=True, chave_acesso="35" + "1" * 42, protocolo="P123", numero=7, serie=1, xml="<xml/>")))
        documento.refresh_from_db()
        self.assertEqual((r["autorizadas"], documento.status, documento.numero, documento.protocolo), (1, "autorizada", 7, "P123"))
        self.assertIsNotNone(documento.autorizado_em)

    def test_resposta_sem_chave_vira_erro_nunca_autorizada(self):
        self.preparar()
        documento = self.pedir()
        r = self.processar(Falso(ResultadoEmissao(autorizada=True)))
        documento.refresh_from_db()
        self.assertEqual((r["erros"], documento.status), (1, "erro"))

    def test_rejeitada_guarda_a_mensagem(self):
        self.preparar()
        documento = self.pedir()
        r = self.processar(Falso(ResultadoEmissao(autorizada=False, rejeitada=True, mensagem="CNPJ do tomador inválido")))
        documento.refresh_from_db()
        self.assertEqual((r["rejeitadas"], documento.status, documento.motivo), (1, "rejeitada", "CNPJ do tomador inválido"))

    def test_erro_temporario_tenta_de_novo_e_esgota(self):
        self.preparar()
        documento = self.pedir()
        falso = Falso(erro=ErroFiscal("indisponível", temporario=True))
        for _ in range(s.MAX_TENTATIVAS):
            DocumentoFiscal.objects.filter(pk=documento.pk).update(proxima_tentativa=timezone.now() - timedelta(minutes=1))
            self.processar(falso)
        documento.refresh_from_db()
        self.assertEqual((documento.status, documento.tentativas), ("erro", s.MAX_TENTATIVAS))

    def test_erro_definitivo_encerra_na_hora(self):
        self.preparar()
        documento = self.pedir()
        self.processar(Falso(erro=ErroFiscal("certificado recusado")))
        documento.refresh_from_db()
        self.assertEqual(documento.status, "erro")

    def test_nao_processa_duas_vezes_o_autorizado(self):
        self.preparar()
        self.pedir()
        falso = Falso(ResultadoEmissao(autorizada=True, chave_acesso="K" * 44, protocolo="P1"))
        self.processar(falso)
        self.processar(falso)
        self.assertEqual(falso.chamadas, 1)

    def test_escopo_por_unidade(self):
        self.preparar()
        documento = self.pedir()
        r = s.processar_pendentes(clientes=type(self.cliente).objects.filter(pk=self.outro.pk))
        documento.refresh_from_db()
        self.assertEqual((r["aguardando"], documento.tentativas, documento.motivo != ""), (0, 0, True))


class CancelamentoTests(FiscalBase):
    def test_pendente_cancela_local_com_motivo(self):
        documento = self.pedir()
        with self.assertRaises(RegraNegocioError):
            s.cancelar_documento(self.req_g, documento, "curto")
        s.cancelar_documento(self.req_g, documento, "Pedido feito por engano pelo caixa")
        documento.refresh_from_db()
        self.assertEqual(documento.status, "cancelada")
        with self.assertRaises(RegraNegocioError):
            s.cancelar_documento(self.req_g, documento, "Pedido feito por engano pelo caixa")
        self.assertIsNotNone(s.solicitar_emissao(self.req_g, self.venda, "nfse")[0])  # cancelado libera novo pedido

    def test_autorizada_exige_o_provedor(self):
        self.preparar()
        documento = self.pedir()
        DocumentoFiscal.objects.filter(pk=documento.pk).update(status="autorizada", chave_acesso="K" * 44, protocolo="P")
        with self.assertRaises(RegraNegocioError):  # provedor real não cancela
            s.cancelar_documento(self.req_g, documento, "Cancelamento solicitado pelo cliente")
        documento.refresh_from_db()
        self.assertEqual(documento.status, "autorizada")
        with mock.patch("apps.fiscal.services.obter", return_value=Falso()):
            s.cancelar_documento(self.req_g, documento, "Cancelamento solicitado pelo cliente")
        documento.refresh_from_db()
        self.assertEqual(documento.status, "cancelada")


class TelasTests(FiscalBase):
    def test_configuracao_salva_cliente_e_unidade_com_auditoria(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(reverse("fiscal-config")).status_code, 200)
        r = self.client.post(reverse("fiscal-config"), {
            "acao": "salvar", "regime_tributario": "mei", "emite_nota_fiscal": "on", "ambiente": "homologacao", "provedor": "nfse_nacional",
            "habilita_nfse": "on", "inscricao_municipal": "99", "inscricao_estadual": "", "codigo_municipio": "3550308", "serie": "2",
        })
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.cliente.refresh_from_db()
        self.config.refresh_from_db()
        self.assertEqual((self.cliente.regime_tributario, self.config.serie, self.config.habilita_nfse), ("mei", 2, True))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="fiscal", acao="editar").count() >= 2)

    def test_codigo_municipio_invalido(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("fiscal-config"), {"acao": "salvar", "regime_tributario": "mei", "ambiente": "homologacao", "provedor": "nenhum", "codigo_municipio": "12", "serie": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("7 dígitos", r.content.decode())

    @cripto
    def test_envio_do_certificado_pela_tela_nao_exibe_segredos(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("fiscal-config"), {"acao": "certificado", "senha": SENHA_PFX, "arquivo": SimpleUploadedFile("c.pfx", gerar_pfx())})
        self.assertEqual(r.status_code, 302)
        html = self.client.get(reverse("fiscal-config")).content.decode()
        self.assertIn("EMPRESA TESTE LTDA", html)
        self.assertNotIn(SENHA_PFX, html)
        r = self.client.post(reverse("fiscal-config"), {"acao": "certificado", "senha": "errada", "arquivo": SimpleUploadedFile("c.pfx", gerar_pfx())})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("errada", r.content.decode())

    def test_operador_nao_configura_nem_pede_nota(self):
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("fiscal-config")).status_code, 403)
        self.assertEqual(self.client.post(reverse("fiscal-solicitar", args=[self.venda.pk]), {"tipo": "nfse"}).status_code, 403)

    def test_solicitar_listar_detalhar_cancelar(self):
        self.client.force_login(self.gestor)
        r = self.client.post(reverse("fiscal-solicitar", args=[self.venda.pk]), {"tipo": "nfse"})
        documento = DocumentoFiscal.objects.get()
        self.assertRedirects(r, reverse("fiscal-documento", args=[documento.pk]))
        self.assertContains(self.client.get(reverse("fiscal-documentos")), "NFS-e")
        self.assertContains(self.client.get(reverse("fiscal-documento", args=[documento.pk])), "NÃO FISCAL" if False else "autoridade fiscal")
        self.client.post(reverse("fiscal-cancelar", args=[documento.pk]), {"motivo": "Cliente desistiu da nota fiscal"})
        documento.refresh_from_db()
        self.assertEqual(documento.status, "cancelada")
        self.assertEqual(self.client.post(reverse("fiscal-processar")).status_code, 302)

    def test_outro_tenant_nao_ve(self):
        documento = self.pedir()
        self.client.force_login(self.gestor_b)
        self.assertEqual(self.client.get(reverse("fiscal-documento", args=[documento.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("fiscal-cancelar", args=[documento.pk]), {"motivo": "x" * 20}).status_code, 404)
        self.assertEqual(self.client.post(reverse("fiscal-solicitar", args=[self.venda.pk]), {"tipo": "nfse"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("fiscal-documentos")), "NFS-e")


class APITests(FiscalBase):
    def setUp(self):
        super().setUp()
        self.api_g, self.api_o, self.api_b = self.api(self.gestor), self.api(self.operador), self.api(self.gestor_b)

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def test_configuracao_sem_segredos(self):
        self.preparar()
        r = self.api_g.get(f"{V1}/fiscal/configuracao/")
        self.assertEqual(r.status_code, 200, r.content[:300])
        dados = r.json()["data"]
        self.assertTrue(dados["certificado"]["enviado"])
        self.assertNotIn(SENHA_PFX, r.content.decode())
        self.assertNotIn("certificado_cifrado", r.content.decode())

    def test_criar_idempotente_listar_cancelar(self):
        corpo = {"venda": self.venda.pk, "tipo": "nfse"}
        r1 = self.api_g.post(f"{V1}/fiscal/documentos/", corpo, format="json")
        r2 = self.api_g.post(f"{V1}/fiscal/documentos/", corpo, format="json")
        self.assertEqual((r1.status_code, r2.status_code), (201, 200), r1.content[:300])
        self.assertEqual(r1.json()["data"]["id"], r2.json()["data"]["id"])
        self.assertEqual(r1.json()["data"]["status"], "pendente")
        pid = r1.json()["data"]["id"]
        self.assertEqual(len(self.api_g.get(f"{V1}/fiscal/documentos/", {"status": "pendente"}).json()["data"]["itens"]), 1)
        self.assertEqual(self.api_g.get(f"{V1}/fiscal/documentos/{pid}/").status_code, 200)
        self.assertEqual(self.api_g.post(f"{V1}/fiscal/documentos/{pid}/cancelar/", {"motivo": "curto"}, format="json").status_code, 422)
        self.assertEqual(self.api_g.post(f"{V1}/fiscal/documentos/{pid}/cancelar/", {"motivo": "Solicitação do cliente final"}, format="json").status_code, 200)

    def test_permissoes_tenant_e_validacao(self):
        corpo = {"venda": self.venda.pk, "tipo": "nfse"}
        self.assertEqual(self.api_o.post(f"{V1}/fiscal/documentos/", corpo, format="json").status_code, 403)
        self.assertEqual(self.api_b.post(f"{V1}/fiscal/documentos/", corpo, format="json").status_code, 400)
        self.assertEqual(self.api_g.post(f"{V1}/fiscal/documentos/", {"venda": self.venda.pk, "tipo": "x"}, format="json").status_code, 422)
        documento = self.pedir()
        self.assertEqual(self.api_b.get(f"{V1}/fiscal/documentos/{documento.pk}/").status_code, 404)
        self.assertEqual(APIClient().get(f"{V1}/fiscal/documentos/").status_code, 401)
