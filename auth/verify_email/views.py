from django.shortcuts import redirect
from django.contrib import messages
from django.conf import settings
from auth.views import AuthView
from auth.models import Profile
from auth.helpers import send_verification_email
import uuid


class VerifyEmailTokenView(AuthView):
    """Confirma o cadastro do usuário a partir do link enviado por e-mail
    (/verify/email/<token>/). O token é o mesmo gravado em
    Profile.email_token por auth.helpers.send_verification_email.
    """

    def get(self, request, token):
        # Profile.objects.filter(...).first() nunca levanta DoesNotExist —
        # por isso o "token inválido" precisa ser checado explicitamente
        # (a versão anterior chamava profile.is_verified direto e quebrava
        # com AttributeError quando o token não existia).
        profile = Profile.objects.filter(email_token=token).first()
        if profile is None:
            messages.error(request, "Token inválido. Tente novamente.")
            return redirect("verify-email-page")

        profile.is_verified = True
        profile.email_token = ""
        profile.save()

        messages.success(request, "E-mail verificado com sucesso.")
        return redirect("login")


class VerifyEmailView(AuthView):
    """Tela "Verifique seu e-mail", exibida após o cadastro (ou quando o
    usuário logado ainda não confirmou o e-mail)."""

    def get(self, request):
        return super().get(request)


class SendVerificationView(AuthView):
    """Gera um novo token e reenvia o e-mail de verificação de cadastro,
    tanto para quem já está logado (mas ainda não verificou) quanto para
    quem acabou de se cadastrar (identificado pela sessão)."""

    def get(self, request):
        email = self._resolve_email(request)
        if not email:
            messages.error(request, "E-mail não encontrado na sessão. Cadastre-se novamente.")
            return redirect("verify-email-page")

        user_profile = Profile.objects.filter(email=email).first()
        if user_profile is None:
            messages.error(request, "Não foi possível localizar o cadastro para este e-mail.")
            return redirect("verify-email-page")

        if not settings.RESEND_API_KEY:
            messages.error(
                request,
                "As configurações de e-mail não estão definidas. Não foi possível enviar o e-mail de verificação.",
            )
            return redirect("verify-email-page")

        token = str(uuid.uuid4())
        user_profile.email_token = token
        user_profile.save()

        send_verification_email(email, token)
        messages.success(request, "E-mail de verificação reenviado com sucesso.")
        return redirect("verify-email-page")

    def _resolve_email(self, request):
        if request.user.is_authenticated:
            return request.user.profile.email
        return request.session.get("email")
