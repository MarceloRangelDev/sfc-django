from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import RegistroAuditoria
from .utils import registrar


@receiver(user_logged_in)
def registrar_login(sender, request, user, **kwargs):
    registrar(request, acao="login", modulo="auth", usuario=user, descricao=f'Login de "{user.username}".')


@receiver(user_logged_out)
def registrar_logout(sender, request, user, **kwargs):
    nome = user.username if user else "desconhecido"
    registrar(request, acao="logout", modulo="auth", usuario=user, descricao=f'Logout de "{nome}".')


@receiver(user_login_failed)
def registrar_login_falho(sender, credentials, request=None, **kwargs):
    # Django dispara este sinal quando authenticate() falha em todos os
    # backends — cobre tanto senha errada quanto usuário inexistente,
    # sem precisar alterar auth/login/views.py.
    identificador = credentials.get("username", "desconhecido")
    registrar(
        request,
        acao="login",
        modulo="auth",
        descricao=f'Tentativa de login falhou para "{identificador}".',
        resultado=RegistroAuditoria.RESULTADO_FALHA,
    )
