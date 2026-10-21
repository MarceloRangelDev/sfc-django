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
        # Mesma mensagem da senha errada: não revela se o usuário existe.
        self.assertIn("Nome de usuário ou senha inválidos.", mensagens)

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


class SegurancaTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        User.objects.create_user(username="usuario1", email="usuario1@teste.local", password="SenhaForte123!")

    def test_ip_ignora_x_forwarded_for_por_padrao(self):
        from django.test import RequestFactory

        from auth.ratelimit import get_client_ip

        requisicao = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="9.9.9.9")
        self.assertEqual(get_client_ip(requisicao), "9.9.9.9")  # cabeçalho forjável não é confiável

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_atras_de_proxy_confiavel_usa_o_ultimo_salto(self):
        from django.test import RequestFactory

        from auth.ratelimit import get_client_ip

        # o cliente tentou forjar "1.1.1.1"; o nginx acrescentou o IP real ao final
        requisicao = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.7", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(get_client_ip(requisicao), "203.0.113.7")

    def test_rate_limit_nao_e_burlado_trocando_x_forwarded_for(self):
        for i in range(3):
            self.client.post(reverse("login"), {"email-username": "usuario1", "password": "errada"}, HTTP_X_FORWARDED_FOR=f"8.8.8.{i}")
        resposta = self.client.post(
            reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!"}, HTTP_X_FORWARDED_FOR="8.8.8.99", follow=True
        )
        mensagens = [str(m) for m in resposta.context["messages"]]
        self.assertTrue(any("Muitas tentativas" in m for m in mensagens), mensagens)

    def test_esqueci_a_senha_responde_igual_para_email_existente_ou_nao(self):
        from django.core.cache import cache

        existente = self.client.post(reverse("forgot-password"), {"email": "usuario1@teste.local"}, follow=True)
        cache.clear()
        inexistente = self.client.post(reverse("forgot-password"), {"email": "ninguem@teste.local"}, follow=True)
        self.assertEqual(
            [str(m) for m in existente.context["messages"]], [str(m) for m in inexistente.context["messages"]]
        )

    def test_cadastro_e_reset_exigem_senha_forte(self):
        resposta = self.client.post(reverse("register"), {"username": "fraco", "email": "fraco@teste.local", "password": "12345678"}, follow=True)
        self.assertFalse(User.objects.filter(username="fraco").exists())
        self.assertTrue(any("senha" in str(m).lower() for m in resposta.context["messages"]))


class LembrarMeTests(TestCase):
    """"Lembrar-me": sessão longa só quando marcado; cookie de sessão sem dados sensíveis."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.usuario = User.objects.create_user(username="usuario1", email="usuario1@teste.local", password="SenhaForte123!")

    def entrar(self, **extra):
        return self.client.post(reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!", **extra})

    def test_com_lembrar_me_a_sessao_dura_trinta_dias(self):
        resposta = self.entrar(**{"remember-me": "1"})
        self.assertEqual(resposta.status_code, 302)
        sessao = self.client.session
        self.assertFalse(sessao.get_expire_at_browser_close())
        self.assertAlmostEqual(sessao.get_expiry_age(), 60 * 60 * 24 * 30, delta=5)
        cookie = resposta.cookies["sessionid"]
        self.assertEqual(int(cookie["max-age"]), 60 * 60 * 24 * 30)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_sem_lembrar_me_o_cookie_expira_ao_fechar_o_navegador(self):
        resposta = self.entrar()
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertEqual(resposta.cookies["sessionid"]["max-age"], "")  # cookie de sessão: sem validade fixa

    def test_cookie_nao_carrega_senha_nem_dados_do_usuario(self):
        resposta = self.entrar(**{"remember-me": "1"})
        valor = resposta.cookies["sessionid"].value
        self.assertNotIn("SenhaForte123", valor)
        self.assertNotIn("usuario1", valor)

    def test_logout_revoga_a_sessao_lembrada(self):
        self.entrar(**{"remember-me": "1"})
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)

    def test_trocar_a_senha_derruba_as_sessoes_antigas(self):
        from django.test import Client

        outro_dispositivo = Client()
        outro_dispositivo.post(reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!", "remember-me": "1"})
        self.assertEqual(outro_dispositivo.get(reverse("index")).status_code, 200)
        self.usuario.set_password("NovaSenhaForte456!")
        self.usuario.save()
        self.assertEqual(outro_dispositivo.get(reverse("index")).status_code, 302)  # hash da sessão não confere mais

    def test_conta_desativada_perde_a_sessao_lembrada(self):
        self.entrar(**{"remember-me": "1"})
        User.objects.filter(pk=self.usuario.pk).update(is_active=False)
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)

    def test_varios_dispositivos_tem_sessoes_independentes(self):
        from django.test import Client

        a, b = Client(), Client()
        for c in (a, b):
            c.post(reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!", "remember-me": "1"})
        a.post(reverse("logout"))
        self.assertEqual(a.get(reverse("index")).status_code, 302)
        self.assertEqual(b.get(reverse("index")).status_code, 200)  # sair de um não derruba o outro

    def test_campo_lembrar_me_chega_ao_servidor_e_tem_aviso(self):
        html = self.client.get(reverse("login")).content.decode()
        self.assertIn('name="remember-me"', html)
        self.assertIn("computadores compartilhados", html)


class RedirecionamentoPosLoginTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        User.objects.create_user(username="usuario1", email="usuario1@teste.local", password="SenhaForte123!")

    def entrar(self, destino):
        return self.client.post(reverse("login"), {"email-username": "usuario1", "password": "SenhaForte123!", "next": destino})

    def test_volta_para_a_pagina_interna_pedida(self):
        self.assertEqual(self.entrar("/insumo").url, "/insumo")

    def test_destino_externo_e_ignorado(self):
        for externo in ("https://malicioso.example/x", "//malicioso.example/x", "javascript:alert(1)"):
            self.client.logout()
            self.assertEqual(self.entrar(externo).url, reverse("index"), externo)


class MensagensEmPortuguesTests(TestCase):
    def test_validacoes_do_formulario_de_login_estao_em_portugues(self):
        from pathlib import Path

        from django.conf import settings

        js = (Path(settings.BASE_DIR) / "src" / "assets" / "js" / "authentication.js").read_text(encoding="utf-8")
        for ingles in ("Please", "must be", "are not the same", "Sending email"):
            self.assertNotIn(ingles, js)

    def test_campos_vazios_mostram_mensagem_em_portugues(self):
        resposta = self.client.post(reverse("login"), {"email-username": "", "password": ""}, follow=True)
        self.assertIn("Por favor, insira seu nome de usuário e senha.", [str(m) for m in resposta.context["messages"]])


class LinkAbsolutoTests(TestCase):
    def test_nao_duplica_a_barra_mesmo_com_base_url_com_barra_final(self):
        from django.test import override_settings

        from .helpers import get_absolute_url

        for base in ("https://app.exemplo.com", "https://app.exemplo.com/"):
            with override_settings(BASE_URL=base):
                self.assertEqual(get_absolute_url("/reset_password/abc/"), "https://app.exemplo.com/reset_password/abc/")
