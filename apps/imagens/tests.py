"""Imagens (R2): validação do conteúdo, upload/substituição/exclusão, falhas parciais, limpeza, isolamento entre tenants, entrega e
variantes, URLs/origem assinadas, auditoria, painel e API do proprietário. O R2 é SEMPRE simulado em memória (nenhuma chamada externa)."""
import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.insumo.tests import novo_insumo
from web_project.testing import habilitar_modulo

from . import armazenamento, custos, processamento, servico
from .models import Imagem, TarifaImagens, TransformacaoUnica

SENHA = "SenhaForte123!"
V1 = "/api/v1"


def imagem_bytes(formato="PNG", tamanho=(300, 200), cor=(200, 30, 30), **extra):
    arquivo = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(arquivo, format=formato, **extra)
    return arquivo.getvalue()


class Base(TestCase):
    def setUp(self):
        cache.clear()  # os contadores de limite usam o cache e o pk dos usuários se repete entre testes
        call_command("seed_acl", verbosity=0)
        self.deposito = armazenamento.ArmazenamentoMemoria()
        patch = mock.patch.object(armazenamento, "_instancia", self.deposito)
        patch.start()
        self.addCleanup(patch.stop)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        gestor = Group.objects.get(name="Gestor")
        for c in (self.a, self.b):
            c.perfis.set([gestor])
            for slug in ("insumo", "produto", "usuario", "cotacao"):
                habilitar_modulo(c, slug)
        self.gestor_a = self.usuario("gestor_a", self.a, "Gestor")
        self.gestor_b = self.usuario("gestor_b", self.b, "Gestor")
        self.dono = User.objects.create_user(username="dono", email="dono@t.local", password=SENHA, is_superuser=True)
        self.admin_global = self.usuario("adm", self.a, "Administrador")
        self.insumo_a = novo_insumo(self.a, "Farinha")
        self.insumo_b = novo_insumo(self.b, "Açúcar")

    def usuario(self, nome, cliente, grupo):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password=SENHA)
        u.groups.add(Group.objects.get(name=grupo))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def logar(self, usuario):
        self.client.logout()
        self.client.login(username=usuario.username, password=SENHA)

    def enviar(self, usuario, tipo, pk, dados=None):
        self.logar(usuario)
        arquivo = SimpleUploadedFile("foto.png", dados or imagem_bytes(), content_type="image/png")
        return self.client.post(reverse("imagens-enviar", args=[tipo, pk]), {"imagem": arquivo})


class ValidacaoTests(TestCase):
    def test_aceita_jpeg_png_webp_e_calcula_metadados(self):
        for formato, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")):
            m, l, a, checksum = processamento.validar(imagem_bytes(formato))
            self.assertEqual((m, l, a, len(checksum)), (mime, 300, 200, 64))

    def test_recusa_svg_texto_vazio_e_corrompido(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        for dados in (svg, b"nao e imagem", b"", imagem_bytes()[:50]):
            with self.assertRaises(processamento.ImagemInvalida):
                processamento.validar(dados)

    def test_conteudo_real_vale_mais_que_a_extensao(self):
        # GIF com nome .png: o formato real (GIF) não é permitido.
        with self.assertRaises(processamento.ImagemInvalida):
            processamento.validar(imagem_bytes("GIF"))

    @override_settings(IMAGENS_MAX_BYTES=1000)
    def test_limite_de_bytes(self):
        with self.assertRaises(processamento.ImagemInvalida):
            processamento.validar(imagem_bytes(tamanho=(400, 400), cor=(1, 2, 3)) + b"0" * 2000)

    @override_settings(IMAGENS_MAX_LADO=100)
    def test_limite_de_dimensoes(self):
        with self.assertRaises(processamento.ImagemInvalida):
            processamento.validar(imagem_bytes(tamanho=(101, 10)))

    @override_settings(IMAGENS_MAX_PIXELS=1000)
    def test_limite_de_pixels_barra_bomba_de_descompressao(self):
        with self.assertRaises(processamento.ImagemInvalida):
            processamento.validar(imagem_bytes(tamanho=(100, 100)))

    def test_variante_reduz_sem_ampliar_e_remove_exif(self):
        exif = Image.Exif()
        exif[0x010F] = "CameraSecreta"
        exif[0x8825] = {1: "S", 2: (23.0, 0.0, 0.0)}  # GPS
        origem = io.BytesIO()
        Image.new("RGB", (2000, 1000), (10, 20, 30)).save(origem, format="JPEG", exif=exif)
        self.assertIn(b"CameraSecreta", origem.getvalue())
        saida = Image.open(io.BytesIO(processamento.gerar_variante(origem.getvalue(), "lista")))
        self.assertEqual(saida.size, (400, 200))
        self.assertEqual(saida.format, "WEBP")
        self.assertEqual(len(saida.getexif()), 0)
        pequena = Image.open(io.BytesIO(processamento.gerar_variante(imagem_bytes(tamanho=(50, 50)), "detalhe")))
        self.assertEqual(pequena.size, (50, 50))
        avatar = Image.open(io.BytesIO(processamento.gerar_variante(imagem_bytes(tamanho=(500, 300)), "avatar")))
        self.assertEqual(avatar.size, (96, 96))

    def test_negociacao_de_formato(self):
        self.assertEqual(processamento.formato_saida("image/webp,*/*")[0], "WEBP")
        if processamento.aceita_avif():
            self.assertEqual(processamento.formato_saida("image/avif,image/webp")[0], "AVIF")
        else:
            self.assertEqual(processamento.formato_saida("image/avif,image/webp")[0], "WEBP")


class CicloDeVidaTests(Base):
    def test_upload_grava_metadados_audita_e_nao_guarda_bytes_no_banco(self):
        resposta = self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        imagem = Imagem.objects.get()
        self.assertEqual((imagem.cliente, imagem.estado, imagem.entidade_tipo, imagem.entidade_id), (self.a, Imagem.ATIVA, "insumo", self.insumo_a.pk))
        self.assertTrue(imagem.chave.startswith(f"t{self.a.pk}/insumo/"))
        self.assertIn(imagem.chave, self.deposito.objetos)
        self.assertNotIn(imagem.chave, resposta.content.decode())  # a chave do R2 não vai ao cliente
        registro = RegistroAuditoria.objects.get(modulo="imagens")
        self.assertEqual((registro.usuario, registro.cliente, registro.acao), (self.gestor_a, self.a, "criar"))

    def test_reenviar_o_mesmo_arquivo_e_idempotente(self):
        dados = imagem_bytes()
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, dados)
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, dados)
        self.assertEqual(Imagem.objects.count(), 1)
        self.assertEqual(len(self.deposito.objetos), 1)

    def test_substituir_mantem_original_antigo_ate_a_retencao_e_a_limpeza_o_remove(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, imagem_bytes(cor=(1, 1, 1)))
        antiga = Imagem.objects.get()
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, imagem_bytes(cor=(9, 9, 9)))
        antiga.refresh_from_db()
        self.assertEqual(antiga.estado, Imagem.PARA_EXCLUIR)
        self.assertEqual(Imagem.objects.filter(estado=Imagem.ATIVA).count(), 1)
        self.assertIn(antiga.chave, self.deposito.objetos)  # retenção
        self.assertEqual(servico.limpar(), (0, 0))  # ainda dentro do prazo
        Imagem.objects.filter(pk=antiga.pk).update(removida_em=timezone.now() - timedelta(days=8))
        self.assertEqual(servico.limpar(), (1, 0))
        self.assertNotIn(antiga.chave, self.deposito.objetos)
        antiga.refresh_from_db()
        self.assertEqual(antiga.estado, Imagem.EXCLUIDA)
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="imagens", acao="editar").count(), 1)

    def test_exclusao_remove_do_r2_e_deixa_de_servir(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        resposta = self.client.post(reverse("imagens-excluir", args=["insumo", self.insumo_a.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.deposito.objetos, {})
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "lista"])).status_code, 404)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="imagens", acao="excluir").exists())

    def test_falha_no_r2_nao_deixa_registro_nem_audita(self):
        with mock.patch.object(self.deposito, "enviar", side_effect=armazenamento.ArmazenamentoIndisponivel("fora")):
            resposta = self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(Imagem.objects.count(), 0)
        self.assertFalse(RegistroAuditoria.objects.filter(modulo="imagens").exists())

    def test_falha_no_banco_depois_do_upload_compensa_removendo_o_objeto(self):
        with mock.patch.object(servico, "_auditar", side_effect=RuntimeError("banco")):
            with self.assertRaises(RuntimeError):
                servico.enviar(self.gestor_a, "insumo", self.insumo_a.pk, imagem_bytes())
        self.assertEqual(self.deposito.objetos, {})
        self.assertFalse(Imagem.objects.filter(estado=Imagem.ATIVA).exists())

    def test_upload_orfao_pendente_e_limpo(self):
        imagem = Imagem.objects.create(
            cliente=self.a, entidade_tipo="insumo", entidade_id=self.insumo_a.pk, chave="t1/insumo/orfao.png", mime="image/png", tamanho=1, largura=1, altura=1, checksum="x",
        )
        self.deposito.enviar(imagem.chave, b"x", "image/png")
        self.assertEqual(servico.limpar(), (0, 0))
        Imagem.objects.filter(pk=imagem.pk).update(criado_em=timezone.now() - timedelta(hours=2))
        self.assertEqual(servico.limpar(), (1, 0))
        self.assertEqual(self.deposito.objetos, {})

    def test_excluir_a_entidade_desvincula_e_segue_a_retencao(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        self.insumo_a.delete()
        imagem = Imagem.objects.get()
        self.assertEqual(imagem.estado, Imagem.PARA_EXCLUIR)
        self.assertIn(imagem.chave, self.deposito.objetos)

    def test_comando_limpar_imagens(self):
        saida = io.StringIO()
        call_command("limpar_imagens", stdout=saida)
        self.assertIn("Objetos removidos: 0", saida.getvalue())

    def test_arquivo_invalido_recebe_400(self):
        resposta = self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, b"<svg></svg>")
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Imagem.objects.count(), 0)


class IsolamentoTests(Base):
    def test_nao_envia_nem_exclui_imagem_de_outro_tenant(self):
        self.assertEqual(self.enviar(self.gestor_b, "insumo", self.insumo_a.pk).status_code, 404)
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        self.logar(self.gestor_b)
        self.assertEqual(self.client.post(reverse("imagens-excluir", args=["insumo", self.insumo_a.pk])).status_code, 404)
        self.assertEqual(Imagem.objects.filter(estado=Imagem.ATIVA).count(), 1)

    def test_nao_le_nem_infere_imagem_de_outro_tenant(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        self.logar(self.gestor_b)
        inexistente = "00000000-0000-0000-0000-000000000000"
        a = self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "lista"]))
        b = self.client.get(reverse("imagens-entrega", args=[inexistente, "lista"]))
        self.assertEqual((a.status_code, b.status_code), (404, 404))  # mesma resposta: não confirma existência

    def test_anonimo_nao_le_e_variante_arbitraria_e_recusada(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "lista"])).status_code, 302)
        self.logar(self.gestor_a)
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "9999x9999"])).status_code, 404)

    def test_modulo_desabilitado_bloqueia(self):
        habilitar_modulo(self.a, "insumo", ativo=False)
        self.assertEqual(self.enviar(self.gestor_a, "insumo", self.insumo_a.pk).status_code, 404)

    def test_usuario_cada_um_troca_a_propria_foto_e_outro_sem_permissao_nao(self):
        operador = self.usuario("op", self.a, "Operador")
        self.assertEqual(self.enviar(operador, "usuario", operador.pk).status_code, 200)
        self.assertEqual(self.enviar(operador, "usuario", self.gestor_a.pk).status_code, 404)
        self.assertEqual(self.enviar(self.gestor_b, "usuario", operador.pk).status_code, 404)

    def test_foto_de_usuario_e_privada_para_quem_nao_tem_acesso(self):
        operador = self.usuario("op", self.a, "Operador")
        self.enviar(operador, "usuario", operador.pk)
        imagem = Imagem.objects.get()
        outro_operador = self.usuario("op2", self.a, "Operador")
        self.logar(outro_operador)
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "avatar"])).status_code, 404)
        self.logar(operador)
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "avatar"])).status_code, 200)

    def test_cliente_exige_permissao_e_tenant(self):
        self.assertEqual(self.enviar(self.gestor_b, "cliente", self.a.pk).status_code, 404)

    def test_tipo_desconhecido_da_404(self):
        self.assertEqual(self.enviar(self.gestor_a, "financeiro", 1).status_code, 404)


class EntregaTests(Base):
    def test_entrega_local_gera_variante_webp_com_cache_privado(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk, imagem_bytes(tamanho=(2000, 1000)))
        imagem = Imagem.objects.get()
        resposta = self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "lista"]), HTTP_ACCEPT="image/webp,*/*")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "image/webp")
        self.assertIn("private", resposta["Cache-Control"])
        self.assertEqual(Image.open(io.BytesIO(resposta.content)).size, (400, 200))
        self.assertEqual(self.deposito.ler(imagem.chave)[:4], b"\x89PNG")  # o original segue intacto

    @override_settings(IMAGENS_ENTREGA="cloudflare", IMAGENS_CF_BASE_URL="https://img.exemplo.test")
    def test_modo_cloudflare_redireciona_catalogo_e_conta_transformacao_unica(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        url = reverse("imagens-entrega", args=[imagem.uuid, "lista"])
        for _ in range(3):
            resposta = self.client.get(url)
            self.assertEqual(resposta.status_code, 302)
        self.assertTrue(resposta["Location"].startswith("https://img.exemplo.test/cdn-cgi/image/"))
        self.assertIn("format=auto", resposta["Location"])
        self.assertIn("metadata=none", resposta["Location"])
        self.assertEqual(TransformacaoUnica.objects.count(), 1)  # repetições no mês contam 1
        self.assertNotIn(imagem.chave, resposta["Location"])
        # A origem entrega o original com o token e nada mais.
        token = resposta["Location"].rsplit("/imagens/o/", 1)[1]
        self.client.logout()
        self.assertEqual(self.client.get(reverse("imagens-origem", args=[token])).status_code, 200)
        self.assertEqual(self.client.get(reverse("imagens-origem", args=[token + "x"])).status_code, 404)
        self.assertEqual(self.client.get("/imagens/o/http://evil.test/x").status_code, 404)

    @override_settings(IMAGENS_ENTREGA="cloudflare", IMAGENS_CF_BASE_URL="https://img.exemplo.test")
    def test_imagem_privada_nunca_sai_pelo_cloudflare_nem_pela_origem(self):
        operador = self.usuario("op", self.a, "Operador")
        self.enviar(operador, "usuario", operador.pk)
        imagem = Imagem.objects.get()
        self.assertEqual(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "avatar"])).status_code, 200)  # servida pelo Django
        token = servico.url_origem(imagem, "").rsplit("/imagens/o/", 1)[1]
        self.client.logout()
        self.assertEqual(self.client.get(reverse("imagens-origem", args=[token])).status_code, 404)

    def test_r2_fora_do_ar_na_leitura_da_503_sem_detalhes(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        with mock.patch.object(self.deposito, "ler", side_effect=armazenamento.ArmazenamentoIndisponivel("x")):
            resposta = self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "detalhe"]))
        self.assertEqual(resposta.status_code, 503)

    def test_cotacao_so_le_a_foto_do_insumo(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        imagem = Imagem.objects.get()
        operador = self.usuario("op", self.a, "Operador")
        self.logar(operador)
        # Operador sem permissão de insumos/cotações não vê; não pode alterar em hipótese alguma.
        self.assertEqual(self.client.post(reverse("imagens-excluir", args=["insumo", self.insumo_a.pk])).status_code, 404)
        self.assertIn(self.client.get(reverse("imagens-entrega", args=[imagem.uuid, "lista"])).status_code, (200, 404))

    @override_settings(IMAGENS_LIMITE_LEITURA_MIN=1)
    def test_limite_de_leituras_por_minuto(self):
        self.enviar(self.gestor_a, "insumo", self.insumo_a.pk)
        url = reverse("imagens-entrega", args=[Imagem.objects.get().uuid, "lista"])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 429)


class ArmazenamentoR2Tests(TestCase):
    @override_settings(R2_ENDPOINT="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY="", R2_BUCKET="")
    def test_sem_configuracao_o_erro_e_claro_e_sem_segredos(self):
        with self.assertRaises(armazenamento.ArmazenamentoIndisponivel) as ctx:
            armazenamento.ArmazenamentoR2()
        self.assertIn("não configurado", str(ctx.exception))

    @override_settings(R2_ENDPOINT="https://conta.r2.cloudflarestorage.com", R2_ACCESS_KEY_ID="AKIAEXEMPLO", R2_SECRET_ACCESS_KEY="segredo-exemplo", R2_BUCKET="bucket-teste")
    def test_url_de_leitura_e_assinada_curta_e_de_um_unico_objeto(self):
        r2 = armazenamento.ArmazenamentoR2()  # não faz chamadas de rede ao ser criado
        url = r2.url_leitura("t1/insumo/x.png", 300)
        self.assertIn("bucket-teste", url)
        self.assertIn("X-Amz-Expires=300", url)
        self.assertIn("t1/insumo/x.png", url)
        self.assertNotIn("segredo-exemplo", url)


class PainelEApiTests(Base):
    def test_painel_so_para_o_proprietario(self):
        for usuario in (self.gestor_a, self.admin_global):
            self.logar(usuario)
            for nome in ("imagens-painel",):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 403, usuario.username)
            self.assertEqual(self.client.post(reverse("imagens-verificar")).status_code, 403)
            self.assertEqual(self.client.post(reverse("imagens-tarifa", args=["classe_a"]), {}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("imagens-painel")).status_code, 302)
        self.logar(self.dono)
        resposta = self.client.get(reverse("imagens-painel"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Estimativa, não é a fatura")

    def test_tarifas_iniciais_tem_fonte_e_data(self):
        self.assertEqual(TarifaImagens.objects.count(), 4)
        for t in TarifaImagens.objects.all():
            self.assertTrue(t.fonte.startswith("https://developers.cloudflare.com/"))
            self.assertEqual(str(t.consultado_em), "2026-10-17")

    def test_dono_altera_tarifa_e_a_alteracao_e_auditada(self):
        self.logar(self.dono)
        dados = {"preco": "5.00", "franquia": "2", "fonte": "https://developers.cloudflare.com/r2/pricing/", "consultado_em": "2026-12-01"}
        self.client.post(reverse("imagens-tarifa", args=["classe_a"]), dados)
        tarifa = TarifaImagens.objects.get(chave="classe_a")
        self.assertEqual((tarifa.preco, tarifa.franquia, tarifa.atualizado_por), (Decimal("5.00"), Decimal("2"), self.dono))
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="imagens", tabela="imagens_tarifaimagens").count(), 1)
        self.client.post(reverse("imagens-tarifa", args=["classe_a"]), {**dados, "preco": "-1"})  # inválida: nada muda
        tarifa.refresh_from_db()
        self.assertEqual(tarifa.preco, Decimal("5.000000"))

    def test_calculo_de_custo_franquia_e_arredondamento(self):
        from .models import UsoImagens

        hoje = timezone.localdate()
        # 12 GB armazenados (franquia 10 GB -> 2 cobrados), 3.5 mi de Classe A (franquia 1 -> 3 cobrados).
        Imagem.objects.create(cliente=self.a, entidade_tipo="insumo", entidade_id=1, chave="k", mime="image/png", tamanho=12 * 10**9, largura=1, altura=1, checksum="x", estado=Imagem.ATIVA)
        UsoImagens.objects.create(dia=hoje, cliente=self.a, metrica="classe_a", quantidade=3_500_000)
        r = custos.relatorio(hoje.year, hoje.month)
        por = {l["rotulo"].split(" - ")[1].split(" (")[0]: l for l in r["linhas"]}
        self.assertEqual(por["armazenamento"]["unidades_cobradas"], 2)
        self.assertEqual(por["armazenamento"]["custo"], Decimal("0.030000"))
        self.assertEqual(por["operações Classe A"]["unidades_cobradas"], 3)
        self.assertEqual(por["operações Classe A"]["custo"], Decimal("13.500000"))
        self.assertEqual(r["total"], Decimal("13.53"))
        self.assertEqual(por["operações Classe B"]["custo"], Decimal("0"))  # dentro da franquia

    def _api(self, usuario):
        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {Token.objects.get_or_create(user=usuario)[0].key}")
        return cliente

    def test_api_somente_proprietario_nas_rotas_de_custo_e_configuracao(self):
        for usuario in (self.gestor_a, self.admin_global):
            api = self._api(usuario)
            for url in ("/plataforma/imagens/configuracao/", "/plataforma/imagens/custos/", "/plataforma/imagens/tarifas/"):
                self.assertEqual(api.get(V1 + url).status_code, 403, (usuario.username, url))
            self.assertEqual(api.patch(V1 + "/plataforma/imagens/tarifas/", {"chave": "classe_a"}, format="json").status_code, 403)
        api = self._api(self.dono)
        config = api.get(V1 + "/plataforma/imagens/configuracao/").json()
        self.assertNotIn("R2_SECRET_ACCESS_KEY", str(config))
        custos_api = api.get(V1 + "/plataforma/imagens/custos/?mes=2026-10")
        self.assertEqual(custos_api.status_code, 200)
        self.assertIn("não substitui a fatura", str(custos_api.json()))
        self.assertEqual(api.get(V1 + "/plataforma/imagens/custos/?mes=abc").status_code, 400)

    def test_api_upload_leitura_e_exclusao_com_isolamento(self):
        api = self._api(self.gestor_a)
        arquivo = SimpleUploadedFile("f.png", imagem_bytes(), content_type="image/png")
        resposta = api.put(f"{V1}/imagens/insumo/{self.insumo_a.pk}/", {"imagem": arquivo}, format="multipart")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        dados = resposta.json()["data"] if "data" in resposta.json() else resposta.json()
        self.assertEqual(set(dados["variantes"]), {"avatar", "miniatura", "lista", "detalhe"})
        self.assertNotIn(Imagem.objects.get().chave, str(dados))
        self.assertEqual(api.get(f"{V1}/imagens/insumo/{self.insumo_a.pk}/").status_code, 200)
        outro = self._api(self.gestor_b)
        self.assertEqual(outro.get(f"{V1}/imagens/insumo/{self.insumo_a.pk}/").status_code, 404)
        self.assertEqual(outro.delete(f"{V1}/imagens/insumo/{self.insumo_a.pk}/").status_code, 404)
        self.assertEqual(api.put(f"{V1}/imagens/insumo/{self.insumo_a.pk}/", {"imagem": SimpleUploadedFile("f.svg", b"<svg/>")}, format="multipart").status_code, 400)
        self.assertEqual(api.delete(f"{V1}/imagens/insumo/{self.insumo_a.pk}/").status_code, 204)
        self.assertEqual(api.get(f"{V1}/imagens/insumo/{self.insumo_a.pk}/").status_code, 404)

    def test_schema_openapi_documenta_as_rotas(self):
        self.logar(self.dono)
        self.dono.groups.add(Group.objects.get(name="Administrador"))
        texto = self.client.get("/api/schema/").content.decode()
        for trecho in ("/api/v1/imagens/{tipo}/{id}/", "/api/v1/plataforma/imagens/custos/", "/api/v1/plataforma/imagens/tarifas/"):
            self.assertIn(trecho, texto)

    def test_campo_de_imagem_aparece_na_edicao_do_insumo(self):
        habilitar_modulo(self.a, "insumo")
        self.logar(self.gestor_a)
        resposta = self.client.get(reverse("insumo-edit", args=[self.insumo_a.pk]))
        self.assertContains(resposta, "data-imagem-campo")
        self.assertContains(resposta, reverse("imagens-enviar", args=["insumo", self.insumo_a.pk]))
