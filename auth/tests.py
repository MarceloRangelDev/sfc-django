"""
Testes do fluxo de autenticação: guarda de sessão (acesso anônimo deve
redirecionar para o login) e login com credenciais válidas/inválidas.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

# Os testes de cadastro disparam o envio de e-mail de verificação
# (auth.helpers.send_verification_email). Sem forçar o backend de e-mail
# em memória aqui, os testes usariam o EMAIL_BACKEND real do projeto
# (ResendEmailBackend, ver config/email_backend.py) e poderiam tentar
# enviar e-mails de verdade através da API do Resend caso RESEND_API_KEY
# esteja configurada no .env — o que nunca deve acontecer durante os testes.
EMAIL_BACKEND_DE_TESTE = "django.core.mail.backends.locmem.EmailBackend"


class GuardaDeSessaoTests(TestCase):
    """A home ("/") é a página mais visível do sistema — precisa exigir
    login como qualquer outra tela protegida (ver Fase 1: apps/sample
    estava sem nenhuma proteção antes desta correção)."""

    def test_usuario_anonimo_e_redirecionado_para_login(self):
        resposta = self.client.get(reverse("index"))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("login"), resposta.url)

    def test_usuario_logado_acessa_a_home(self):
        User.objects.create_user(username="usuario1", password="SenhaForte123!")
        self.client.login(username="usuario1", password="SenhaForte123!")
        resposta = self.client.get(reverse("index"))
        self.assertEqual(resposta.status_code, 200)


class LoginTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username="usuario1", email="usuario1@teste.local", password="SenhaForte123!"
        )

    def test_login_com_credenciais_validas_funciona(self):
        resposta = self.client.post(
            reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!"}
        )
        self.assertEqual(resposta.status_code, 302)
        resposta_home = self.client.get(reverse("index"))
        self.assertTrue(resposta_home.wsgi_request.user.is_authenticated)

    def test_login_com_senha_invalida_mostra_mensagem_em_pt_br(self):
        resposta = self.client.post(
            reverse("login"), {"email-username": "usuario1", "password": "senha-errada"}, follow=True
        )
        mensagens = [str(m) for m in resposta.context["messages"]]
        self.assertIn("Nome de usuário ou senha inválidos.", mensagens)

    def test_login_com_usuario_inexistente_mostra_mensagem_em_pt_br(self):
        resposta = self.client.post(
            reverse("login"), {"email-username": "nao-existe", "password": "qualquer"}, follow=True
        )
        mensagens = [str(m) for m in resposta.context["messages"]]
        self.assertIn("Por favor, insira um nome de usuário válido.", mensagens)

    def test_logout_invalida_a_sessao(self):
        self.client.login(username="usuario1", password="SenhaForte123!")
        self.client.post(reverse("logout"))
        resposta = self.client.get(reverse("index"))
        self.assertEqual(resposta.status_code, 302)


@override_settings(EMAIL_BACKEND=EMAIL_BACKEND_DE_TESTE)
class RegistroTests(TestCase):
    def test_cadastro_com_email_duplicado_e_rejeitado(self):
        User.objects.create_user(username="existente", email="usado@teste.local", password="SenhaForte123!")
        resposta = self.client.post(
            reverse("register"),
            {"username": "novo", "email": "usado@teste.local", "password": "SenhaForte123!"},
            follow=True,
        )
        mensagens = [str(m) for m in resposta.context["messages"]]
        self.assertIn("Este e-mail já está cadastrado.", mensagens)

    def test_cadastro_novo_entra_no_grupo_visualizador(self):
        self.client.post(
            reverse("register"),
            {"username": "novato", "email": "novato@teste.local", "password": "SenhaForte123!"},
        )
        usuario = User.objects.get(username="novato")
        self.assertTrue(usuario.groups.filter(name="Visualizador").exists())
