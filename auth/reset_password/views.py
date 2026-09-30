from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.utils import timezone

from auth.models import Profile
from auth.views import AuthView


class ResetPasswordView(AuthView):
    """Tela de redefinição de senha, acessada pelo link enviado por e-mail
    (/reset_password/<token>/). O token é o mesmo gravado em
    Profile.forget_password_token pela tela "Esqueci minha senha".
    """

    def _get_valid_profile(self, token):
        """Busca o Profile pelo token garantindo que ele ainda não expirou.

        Retorna None se o token não existir ou já tiver passado da validade
        (Profile.forget_password_token_expiration) — sem esta checagem o
        link de redefinição de senha nunca expirava (falha de segurança).
        """
        profile = Profile.objects.filter(forget_password_token=token).first()
        if profile is None or not token:
            return None
        if not profile.forget_password_token_expiration:
            return None
        if profile.forget_password_token_expiration < timezone.now():
            return None
        return profile

    def get(self, request, token):
        if request.user.is_authenticated:
            return redirect("index")

        # Diferente da versão anterior, já validamos o token no GET: um
        # link inválido/expirado não deve nem mostrar o formulário de nova
        # senha, só a mensagem de erro.
        profile = self._get_valid_profile(token)
        if profile is None:
            messages.error(
                request,
                "Este link de redefinição de senha é inválido ou já expirou. Solicite um novo.",
            )
            return redirect("forgot-password")

        return render(request, self.template_name, {"token": token})

    def post(self, request, token):
        profile = self._get_valid_profile(token)
        if profile is None:
            messages.error(
                request,
                "Este link de redefinição de senha é inválido ou já expirou. Solicite um novo.",
            )
            return redirect("forgot-password")

        new_password = request.POST.get("password")
        confirm_password = request.POST.get("confirm-password")

        if not (new_password and confirm_password):
            messages.error(request, "Preencha todos os campos.")
            return render(request, self.template_name, {"token": token})

        if new_password != confirm_password:
            messages.error(request, "As senhas não coincidem.")
            return render(request, self.template_name, {"token": token})

        user = profile.user
        user.set_password(new_password)
        user.save()

        # Token de uso único: depois de usado, não pode ser reaproveitado
        # (nem por um segundo POST acidental na mesma URL).
        profile.forget_password_token = ""
        profile.forget_password_token_expiration = None
        profile.save()

        authenticated_user = authenticate(request, username=user.username, password=new_password)
        if authenticated_user:
            login(request, authenticated_user)
            return redirect("index")

        messages.success(request, "Senha redefinida com sucesso. Faça login novamente.")
        return redirect("login")
