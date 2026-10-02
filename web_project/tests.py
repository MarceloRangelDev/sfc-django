"""Testes de encoding (UTF-8) e de idioma do Vuexy — regressões da Fase 1."""
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

MOJIBAKE = ("Ã©", "Ã³", "Ã§", "Ã£", "Ãº", "Ã­", "â€")


class MenuEncodingTests(SimpleTestCase):
    def test_menu_json_e_utf8_e_sem_mojibake(self):
        """O menu é lido com encoding explícito; 'Papéis' não pode virar 'PapÃ©is'."""
        from templates.layout.bootstrap.layout_vertical import menu_file_path

        texto = menu_file_path.read_text(encoding="utf-8")
        self.assertIn("Papéis", texto)
        for lixo in MOJIBAKE:
            self.assertNotIn(lixo, texto)
        json.loads(texto)

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
