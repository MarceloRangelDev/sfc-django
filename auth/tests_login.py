"""Login: a tela não revela regras internas de senha/usuário (tamanho mínimo) e o servidor continua validando com mensagens neutras."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

JS = Path(settings.BASE_DIR) / "src" / "assets" / "js" / "authentication.js"


class TelaDeLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user(username="maria", email="maria@t.local", password="SenhaForte123!")

    def test_html_da_tela_nao_traz_regra_de_tamanho(self):
        pagina = self.client.get(reverse("login"))
        self.assertEqual(pagina.status_code, 200)
        for texto in ("pelo menos 4 caracteres", "deve ter pelo menos", "minlength"):
            self.assertNotContains(pagina, texto)

    def test_script_do_login_nao_valida_tamanho_de_usuario_nem_de_senha(self):
        js = JS.read_text(encoding="utf-8")
        campo_usuario = js[js.index("'email-username'"):js.index("password: {")]
        self.assertNotIn("stringLength", campo_usuario)
        self.assertNotIn("pelo menos 4 caracteres", campo_usuario)
        # a senha só exibe a regra fora da tela de login (cadastro/redefinição), onde o usuário precisa conhecê-la
        campo_senha = js[js.index("password: {"):js.index("'confirm-password'")]
        self.assertIn("ehTelaDeLogin", campo_senha)
        self.assertIn("const ehTelaDeLogin", js)

    def test_servidor_responde_mensagem_neutra_para_senha_curta_ou_errada(self):
        for senha in ("a", "SenhaErrada999"):
            resposta = self.client.post(reverse("login"), {"email-username": "maria", "password": senha}, follow=True)
            textos = [str(m) for m in resposta.context["messages"]]
            self.assertEqual(textos, ["Nome de usuário ou senha inválidos."])
            self.assertNotIn("caracteres", " ".join(textos))

    def test_servidor_continua_exigindo_usuario_e_senha(self):
        resposta = self.client.post(reverse("login"), {"email-username": "maria", "password": ""}, follow=True)
        self.assertEqual([str(m) for m in resposta.context["messages"]], ["Por favor, insira seu nome de usuário e senha."])
        self.assertFalse("_auth_user_id" in self.client.session)

    def test_login_valido_continua_funcionando(self):
        resposta = self.client.post(reverse("login"), {"email-username": "maria", "password": "SenhaForte123!"})
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
