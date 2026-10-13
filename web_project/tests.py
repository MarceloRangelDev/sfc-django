"""Testes de encoding (UTF-8) e de idioma do Vuexy — regressões da Fase 1."""
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

MOJIBAKE = ("Ã©", "Ã³", "Ã§", "Ã£", "Ãº", "Ã­", "â€")


class MenuEncodingTests(SimpleTestCase):
    def test_menu_json_e_utf8_e_sem_mojibake(self):
        """Os itens estáticos do menu (JSON) e o catálogo de módulos usam UTF-8 sem mojibake."""
        from apps.modulo.management.commands.seed_modulos import MODULOS_PADRAO
        from templates.layout.bootstrap.layout_vertical import menu_file_path

        texto = menu_file_path.read_text(encoding="utf-8")
        json.loads(texto)
        nomes = " ".join(m["nome"] for m in MODULOS_PADRAO.values())
        self.assertIn("Papéis", nomes)  # o menu dinâmico vem do catálogo de módulos
        for lixo in MOJIBAKE:
            self.assertNotIn(lixo, texto)
            self.assertNotIn(lixo, nomes)

    def test_layouts_nao_usam_open_sem_encoding(self):
        for nome in ("layout_vertical.py", "layout_horizontal.py"):
            fonte = (Path(settings.BASE_DIR) / "templates/layout/bootstrap" / nome).read_text(encoding="utf-8")
            self.assertNotIn("menu_file_path.open()", fonte)


class IdiomaEHeadersTests(TestCase):
    def test_login_declara_utf8_e_pt_br_no_html(self):
        resposta = self.client.get("/login/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("utf-8", resposta["Content-Type"].lower())
        html = resposta.content.decode("utf-8")
        self.assertIn('<html lang="pt-br"', html)
        self.assertIn("template-customizer-pt-br.js", html)

    def test_arquivo_de_idioma_do_customizer_existe(self):
        arquivo = Path(settings.BASE_DIR) / "src/assets/js/template-customizer-pt-br.js"
        self.assertIn("LANGUAGES['pt-br']", arquivo.read_text(encoding="utf-8"))


class PdfPadraoTests(SimpleTestCase):
    """Padrão único dos relatórios em PDF: várias páginas, cabeçalho de colunas repetido, rodapé com a empresa
    e "Página X de Y", textos longos sem cortes e ausência de dados quando não há linhas."""

    def setUp(self):
        from types import SimpleNamespace

        from reportlab import rl_config

        self._compressao = rl_config.pageCompression
        rl_config.pageCompression = 0  # texto legível no arquivo, para conferir o conteúdo
        self.usuario = SimpleNamespace(get_username=lambda: "maria", profile=SimpleNamespace(cliente=SimpleNamespace(nome_razao_social="Empresa Teste Ltda")))
        self.cabecalho = ["Código", "Insumo", "Valor", "Status"]

    def tearDown(self):
        from reportlab import rl_config

        rl_config.pageCompression = self._compressao

    def gerar(self, linhas, filtros=""):
        from web_project.exportacao import exportar_pdf

        resposta = exportar_pdf("teste", "Relatório de Teste", self.usuario, self.cabecalho, linhas, filtros)
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertIn('filename="teste.pdf"', resposta["Content-Disposition"])
        return resposta.content

    def test_varias_paginas_com_paginacao_e_empresa_no_rodape(self):
        linhas = [[f"INS-{i:05d}", f"Insumo {i} " + "muito comprido " * 8, "R$ 1,00", "Ativo"] for i in range(150)]
        pdf = self.gerar(linhas)
        paginas = pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") + pdf.count(b"/Type /Page/")
        self.assertGreaterEqual(paginas, 3)
        total = paginas
        # os acentos saem escapados no arquivo (\341); confere o resto do texto do rodapé
        self.assertIn(f"gina 1 de {total}".encode(), pdf)
        self.assertIn(f"gina {total} de {total}".encode(), pdf)
        self.assertGreaterEqual(pdf.count(b"Empresa Teste Ltda"), total)  # rodapé em todas as páginas
        self.assertGreaterEqual(pdf.count(b"SISTEMA FATURANDO CERTO"), 1)
        self.assertGreaterEqual(pdf.count(b"digo)"), total)  # cabeçalho de colunas ("Código") repetido em cada página

    def test_marcacao_e_texto_do_usuario_nao_quebram_o_pdf(self):
        pdf = self.gerar([["<b>x</b> & <script>", "A&B", "R$ 1,00", "Ativo"]], filtros="q=<x>")
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_sem_registros_mostra_mensagem(self):
        self.assertIn(b"Nenhum registro encontrado.", self.gerar([]))

    def test_filtros_aparecem_no_cabecalho(self):
        self.assertIn(b"Filtros: q=abc", self.gerar([["1", "a", "R$ 1,00", "Ativo"]], filtros="q=abc"))


class StaticVersionadoTests(SimpleTestCase):
    """{% static_v %} acrescenta a data do arquivo à URL, para o navegador não usar um .js antigo do cache."""

    def render(self, caminho):
        from django.template import Context, Template

        return Template("{% static_v '" + caminho + "' %}").render(Context())

    def test_arquivo_proprio_recebe_a_versao(self):
        import re

        url = self.render("js/ficha-form.js")
        self.assertRegex(url, r"^/static/js/ficha-form\.js\?v=\d+$")
        self.assertTrue(re.search(r"v=\d{9,}", url))

    def test_arquivo_inexistente_devolve_a_url_normal(self):
        self.assertEqual(self.render("js/nao-existe.js"), "/static/js/nao-existe.js")

    def test_paginas_usam_a_versao_nos_scripts_proprios(self):
        from pathlib import Path

        from django.conf import settings

        pendentes = []
        for modelo in Path(settings.BASE_DIR).glob("**/templates/**/*.html"):
            if ".venv" in modelo.parts:
                continue
            if "{% static 'js/" in modelo.read_text(encoding="utf-8"):
                pendentes.append(str(modelo))
        self.assertEqual(pendentes, [])
