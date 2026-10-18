"""Insumos globais (padronização), fila de não conformidade, thumbs, exportação sem Código/Status, logo nos PDFs,
foto do perfil por permissão e menu lateral oculto por papel."""
import io
from unittest import mock

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from apps.acl.models import PapelConfig
from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.imagens import armazenamento
from apps.insumo.models import Insumo
from apps.insumo.tests import medida, novo_insumo
from apps.notificacao.models import DestinatarioNotificacao
from web_project.testing import habilitar_modulo

from .models import InsumoGlobal, normalizar

SENHA = "SenhaForte123!"


def png():
    arquivo = io.BytesIO()
    Image.new("RGB", (200, 120), (10, 120, 200)).save(arquivo, format="PNG")
    return arquivo.getvalue()


class Base(TestCase):
    def setUp(self):
        call_command("seed_acl", verbosity=0)
        self.deposito = armazenamento.ArmazenamentoMemoria()
        patch = mock.patch.object(armazenamento, "_instancia", self.deposito)
        patch.start()
        self.addCleanup(patch.stop)
        self.a = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        self.b = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Empresa B")
        for c in (self.a, self.b):
            c.perfis.set(Group.objects.filter(name__in=["Gestor", "Operador", "Administrador"]))
            for slug in ("insumo", "produto", "usuario"):
                habilitar_modulo(c, slug)
        self.gestor = self.usuario("gestor", self.a, "Gestor")
        self.admin = self.usuario("admin", self.a, "Administrador")
        self.acucar = InsumoGlobal.objects.create(nome="Açúcar", medida=medida("kg"))

    def usuario(self, nome, cliente, grupo):
        u = User.objects.create_user(username=nome, email=f"{nome}@t.local", password=SENHA)
        u.groups.add(Group.objects.get(name=grupo))
        u.profile.cliente = cliente
        u.profile.save()
        return u

    def logar(self, usuario):
        self.client.logout()
        self.client.login(username=usuario.username, password=SENHA)

    def cadastrar(self, nome, **extra):
        dados = {"nome": nome, "marca": "", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": "10,00", "ativo": "on", **extra}
        return self.client.post(reverse("insumo-create"), dados)


class PadronizacaoNoCadastroTests(Base):
    def test_normalizar_ignora_acento_caixa_e_espacos(self):
        self.assertEqual(normalizar("  AÇÚCAR   Refinado "), "acucar refinado")

    def test_nome_padronizado_usa_o_nome_do_padrao_e_nao_gera_aviso(self):
        self.logar(self.gestor)
        self.cadastrar("acucar")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.nome, insumo.insumo_global, insumo.nao_conforme), ("Açúcar", self.acucar, False))
        self.assertFalse(DestinatarioNotificacao.objects.filter(notificacao__titulo="Insumo fora do padrão").exists())

    def test_nome_parecido_exige_confirmacao_e_depois_fica_fora_do_padrao_com_aviso(self):
        self.logar(self.gestor)
        resposta = self.cadastrar("Asucar")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Existe no cadastro padrão")
        self.assertFalse(Insumo.objects.filter(cliente=self.a).exists())
        self.cadastrar("Asucar", confirmar_novo="on")
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.nome, insumo.insumo_global, insumo.nao_conforme), ("Asucar", None, True))
        # Administradores (perfil Administrador e superusuários) recebem a notificação; o Gestor não.
        destinatarios = set(DestinatarioNotificacao.objects.filter(notificacao__titulo="Insumo fora do padrão").values_list("usuario__username", flat=True))
        self.assertIn("admin", destinatarios)
        self.assertNotIn("gestor", destinatarios)

    def test_insumo_totalmente_novo_e_salvo_como_fora_do_padrao_e_avisa(self):
        self.logar(self.gestor)
        self.cadastrar("Cúrcuma Defumada")
        self.assertTrue(Insumo.objects.get(cliente=self.a).nao_conforme)
        self.assertTrue(DestinatarioNotificacao.objects.filter(usuario=self.admin, notificacao__titulo="Insumo fora do padrão").exists())

    def test_editar_sem_trocar_o_nome_nao_reavalia(self):
        self.logar(self.gestor)
        self.cadastrar("Cúrcuma Defumada")
        insumo = Insumo.objects.get(cliente=self.a)
        DestinatarioNotificacao.objects.all().delete()
        self.client.post(reverse("insumo-edit", args=[insumo.pk]), {"nome": "Cúrcuma Defumada", "marca": "X", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": "12,00", "ativo": "on"})
        insumo.refresh_from_db()
        self.assertEqual((insumo.marca, insumo.nao_conforme), ("X", True))
        self.assertFalse(DestinatarioNotificacao.objects.exists())


class TelasDoAdminTests(Base):
    def fora_do_padrao(self, cliente=None, nome="Asucar"):
        return Insumo.objects.create(cliente=cliente or self.a, nome=nome, medida=medida("kg"), quantidade=1, preco_pago=1000, nao_conforme=True)

    def test_so_quem_tem_a_permissao_acessa(self):
        for nome in ("insumo-global", "insumo-nao-conformes"):
            self.logar(self.gestor)
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
            self.logar(self.admin)
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        # Permissão concedida individualmente (papel Gestor + exceção) libera.
        from apps.acl.models import PermissaoUsuario

        PermissaoUsuario.objects.create(usuario=self.gestor, permissao=Permission.objects.get(codename="view_naoconformes"), efeito="permitir")
        self.logar(self.gestor)
        self.assertEqual(self.client.get(reverse("insumo-nao-conformes")).status_code, 200)
        self.assertEqual(self.client.post(reverse("insumo-nao-conforme-promover", args=[1])).status_code, 403)  # ver != padronizar

    def test_aparecem_nas_matrizes_de_papeis(self):
        from apps.acl.permissions_matrix import montar_matriz_permissoes

        nomes = [linha["nome"] for linha in montar_matriz_permissoes()]
        for esperado in ("Insumos globais", "Insumos fora do padrão", "Foto do perfil", "Painel"):
            self.assertIn(esperado, nomes)

    def test_lista_mostra_sugestoes(self):
        self.fora_do_padrao()
        self.logar(self.admin)
        self.assertContains(self.client.get(reverse("insumo-nao-conformes")), "Açúcar")

    def test_redirecionar_renomeia_quando_o_cliente_nao_tem_o_padrao(self):
        insumo = self.fora_do_padrao()
        self.logar(self.admin)
        self.client.post(reverse("insumo-nao-conforme-redirecionar", args=[insumo.pk]), {"destino": self.acucar.pk})
        insumo.refresh_from_db()
        self.assertEqual((insumo.nome, insumo.insumo_global, insumo.nao_conforme), ("Açúcar", self.acucar, False))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="insumo", registro_id=str(insumo.pk), acao="editar").exists())

    def test_redirecionar_mescla_e_exclui_o_duplicado_quando_o_cliente_ja_tem_o_padrao(self):
        from apps.ficha.models import Ficha, FichaItem
        from apps.ficha.tests import criar as criar_ficha  # noqa: F401  (garante o app de fichas carregado)

        padrao = novo_insumo(self.a, "Açúcar", "kg", "1", 500)
        padrao.insumo_global = self.acucar
        padrao.save()
        errado = self.fora_do_padrao()
        self.logar(self.admin)
        self.client.post(reverse("insumo-nao-conforme-redirecionar", args=[errado.pk]), {"destino": self.acucar.pk})
        errado = Insumo.objects.filter(pk=errado.pk).first()  # excluído, ou inativado se houver histórico imutável
        self.assertTrue(errado is None or (not errado.ativo and not errado.nao_conforme))
        self.assertTrue(Insumo.objects.filter(pk=padrao.pk).exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="insumo", acao="excluir").exists())

    def test_promover_cria_um_novo_padrao(self):
        insumo = self.fora_do_padrao(nome="Cúrcuma")
        self.logar(self.admin)
        self.client.post(reverse("insumo-nao-conforme-promover", args=[insumo.pk]))
        insumo.refresh_from_db()
        self.assertEqual((insumo.nao_conforme, insumo.insumo_global.nome), (False, "Cúrcuma"))
        self.assertTrue(InsumoGlobal.objects.filter(nome="Cúrcuma").exists())

    def test_cadastro_global_crud_e_nome_unico(self):
        self.logar(self.admin)
        self.assertEqual(self.client.post(reverse("insumo-global-create"), {"nome": "AÇUCAR", "ativo": "on"}).status_code, 200)  # duplicado (sem acento/caixa)
        criado = self.client.post(reverse("insumo-global-create"), {"nome": "Farinha", "ativo": "on"})
        farinha = InsumoGlobal.objects.get(nome="Farinha")
        self.assertRedirects(criado, reverse("insumo-global-edit", args=[farinha.pk]))  # abre a edição para enviar a foto
        self.assertContains(self.client.get(reverse("insumo-global-edit", args=[farinha.pk])), "data-imagem-campo")
        self.client.post(reverse("insumo-global-alternar", args=[farinha.pk]))
        farinha.refresh_from_db()
        self.assertFalse(farinha.ativo)

    def test_foto_do_padrao_aparece_no_insumo_do_cliente_sem_foto(self):
        self.logar(self.admin)
        arquivo = SimpleUploadedFile("a.png", png(), content_type="image/png")
        resposta = self.client.post(reverse("imagens-enviar", args=["insumo_global", self.acucar.pk]), {"imagem": arquivo})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        insumo = novo_insumo(self.a, "Açúcar", "kg", "1", 500)
        insumo.insumo_global = self.acucar
        insumo.save()
        pagina = self.client.get(reverse("insumo")).content.decode()
        self.assertIn("/imagens/", pagina)
        self.assertNotIn("tabler-photo-off", pagina)


class ThumbsEExportacaoTests(Base):
    def test_lista_de_insumos_e_produtos_mostram_icone_sem_foto(self):
        novo_insumo(self.a, "Farinha", "kg", "1", 500)
        self.logar(self.gestor)
        self.assertContains(self.client.get(reverse("insumo")), "tabler-photo-off")
        self.assertContains(self.client.get(reverse("produto")), "<th>Foto</th>")

    def test_miniatura_aparece_quando_ha_foto(self):
        insumo = novo_insumo(self.a, "Farinha", "kg", "1", 500)
        self.logar(self.gestor)
        arquivo = SimpleUploadedFile("f.png", png(), content_type="image/png")
        self.assertEqual(self.client.post(reverse("imagens-enviar", args=["insumo", insumo.pk]), {"imagem": arquivo}).status_code, 200)
        pagina = self.client.get(reverse("insumo")).content.decode()
        self.assertIn("/miniatura", pagina)
        self.assertNotIn("tabler-photo-off", pagina)

    def test_excel_e_pdf_dos_insumos_sem_codigo_e_status_e_o_pdf_tem_logo(self):
        novo_insumo(self.a, "Farinha", "kg", "1", 500)
        self.logar(self.gestor)
        xlsx = self.client.get(reverse("insumo"), {"export": "xlsx"})
        linhas = list(openpyxl.load_workbook(io.BytesIO(xlsx.content)).active.values)
        self.assertEqual(list(linhas[0]), ["Insumo", "Marca", "Unidade", "Quantidade", "Valor pago", "Preço", "Cadastrado em"])
        self.assertEqual(linhas[1][0], "Farinha")
        pdf = self.client.get(reverse("insumo"), {"export": "pdf"}).content
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn(b"/Subtype /Image", pdf)  # logo embutida no cabeçalho

    def test_logo_em_todos_os_pdfs(self):
        from apps.pdv.comprovante import resposta_pdf

        for formato in ("a4", "termica80", "termica58"):
            dados = {"numero": 1, "formato_padrao": formato, "linhas": []}
            with mock.patch("apps.pdv.comprovante.linhas_texto", return_value=["COMPROVANTE", "linha"]):
                pdf = resposta_pdf(dados, formato).content
            self.assertIn(b"/Subtype /Image", pdf, formato)


class FotoDoPerfilTests(Base):
    def enviar(self, usuario, alvo):
        self.logar(usuario)
        arquivo = SimpleUploadedFile("p.png", png(), content_type="image/png")
        return self.client.post(reverse("imagens-enviar", args=["usuario", alvo.pk]), {"imagem": arquivo})

    def test_usuario_troca_a_propria_foto_com_a_permissao(self):
        operador = self.usuario("op", self.a, "Operador")
        self.assertEqual(self.enviar(operador, operador).status_code, 200)
        self.assertContains(self.client.get(reverse("profile")), "data-imagem-campo")
        self.assertContains(self.client.get(reverse("index"), follow=True), "/imagens/")  # avatar na navbar

    def test_sem_a_permissao_nao_troca_nem_ve_o_campo(self):
        operador = self.usuario("op", self.a, "Operador")
        operador.groups.first().permissions.remove(Permission.objects.get(codename="change_fotoperfil"))
        operador = User.objects.get(pk=operador.pk)
        self.assertEqual(self.enviar(operador, operador).status_code, 404)
        self.assertNotContains(self.client.get(reverse("profile")), "data-imagem-campo")


class MenuLateralPorPapelTests(Base):
    def test_papel_sem_menu_oculta_a_barra_e_a_home_vai_para_o_pdv(self):
        caixa_grupo = Group.objects.create(name="Caixa")
        caixa_grupo.permissions.set(Permission.objects.filter(codename__in=["view_telavender", "add_venda", "view_venda", "abrir_caixa", "view_caixa", "view_telameucaixa"]))
        self.a.perfis.add(caixa_grupo)
        for slug in ("pdv",):
            habilitar_modulo(self.a, slug)
        caixa = User.objects.create_user(username="caixa", email="caixa@t.local", password=SENHA)
        caixa.groups.add(caixa_grupo)
        caixa.profile.cliente = self.a
        caixa.profile.save()
        self.logar(caixa)
        pagina = self.client.get(reverse("pdv-caixa")).content.decode()
        self.assertIn('id="layout-menu"', pagina)  # ainda visível: papel não oculta
        PapelConfig.objects.create(group=caixa_grupo, ocultar_menu_lateral=True)
        pagina = self.client.get(reverse("pdv-caixa")).content.decode()
        self.assertNotIn('id="layout-menu"', pagina)
        self.assertIn("layout-without-menu", pagina)
        self.assertIn('id="btn-tela-cheia"', pagina)
        self.assertRedirects(self.client.get(reverse("index")), reverse("pdv"), fetch_redirect_response=False)

    def test_superusuario_sempre_ve_o_menu_e_a_tela_de_papeis_grava_a_opcao(self):
        dono = User.objects.create_user(username="dono", email="dono@t.local", password=SENHA, is_superuser=True)
        self.logar(dono)
        grupo = Group.objects.get(name="Operador")
        self.client.post(reverse("acl-papel-edit", args=[grupo.pk]), {"nome": "Operador", "ocultar_menu_lateral": "on", "permissoes": [p.pk for p in grupo.permissions.all()]})
        self.assertTrue(PapelConfig.objects.get(group=grupo).ocultar_menu_lateral)
        self.assertIn('id="layout-menu"', self.client.get(reverse("index")).content.decode())
        self.assertContains(self.client.get(reverse("acl-papel-edit", args=[grupo.pk])), "ocultar_menu_lateral")
