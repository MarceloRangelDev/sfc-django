from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login

from auth.services import ErroDeAutenticacao, perfil_por_token_de_redefinicao, redefinir_senha
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
        return perfil_por_token_de_redefinicao(token)

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

        return render(request, self.template_name, self.get_context_data(token=token))  # inclui layout_path (o template estende dele)

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
            return render(request, self.template_name, self.get_context_data(token=token))  # inclui layout_path (o template estende dele)

        if new_password != confirm_password:
            messages.error(request, "As senhas não coincidem.")
            return render(request, self.template_name, self.get_context_data(token=token))  # inclui layout_path (o template estende dele)

        # O serviço valida a força da senha (AUTH_PASSWORD_VALIDATORS), troca a
        # senha e invalida o token (uso único) numa transação.
        try:
            user = redefinir_senha(token, new_password)
        except ErroDeAutenticacao as erro:
            messages.error(request, erro.mensagem)
            return render(request, self.template_name, self.get_context_data(token=token))  # inclui layout_path (o template estende dele)

        authenticated_user = authenticate(request, username=user.username, password=new_password)
        if authenticated_user:
            login(request, authenticated_user)
            return redirect("index")

        messages.success(request, "Senha redefinida com sucesso. Faça login novamente.")
        return redirect("login")
