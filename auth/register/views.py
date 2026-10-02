from django.shortcuts import redirect
from django.contrib import messages
from django.conf import settings
from auth.views import AuthView
from auth.services import ErroDeAutenticacao, cadastrar_usuario_publico


class RegisterView(AuthView):
    def get(self, request):
        if request.user.is_authenticated:
            # Usuário já logado não precisa desta tela.
            return redirect("index")

        return super().get(request)

    def post(self, request):
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")

        # Toda a regra de cadastro (duplicidade, força da senha, grupo
        # "Visualizador", token e e-mail de verificação) vive em
        # auth/services.py, compartilhada com a API.
        try:
            cadastrar_usuario_publico(username, email, password)
        except ErroDeAutenticacao as erro:
            messages.error(request, erro.mensagem)
            return redirect("register")

        if settings.RESEND_API_KEY:
            messages.success(request, "E-mail de verificação enviado com sucesso.")
        else:
            messages.error(
                request,
                "As configurações de e-mail não estão definidas. Não foi possível enviar o e-mail de verificação.",
            )

        request.session['email'] = email  # Guarda o e-mail na sessão para a tela de verificação.
        return redirect("verify-email-page")
