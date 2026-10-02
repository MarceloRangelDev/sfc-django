from django.shortcuts import redirect
from django.contrib import messages
from django.conf import settings
from auth.services import iniciar_redefinicao_de_senha
from auth.views import AuthView
from auth import ratelimit


class ForgetPasswordView(AuthView):
    """Tela "Esqueci minha senha": recebe o e-mail e, se existir um
    usuário cadastrado, gera um token de redefinição com validade de
    24 horas e envia por e-mail o link para criar uma nova senha.
    """

    def get(self, request):
        if request.user.is_authenticated:
            # Usuário já logado não precisa desta tela.
            return redirect("index")

        return super().get(request)

    def post(self, request):
        email = request.POST.get("email")

        if not email:
            messages.error(request, "Por favor, informe um e-mail.")
            return redirect("forgot-password")

        ip = ratelimit.get_client_ip(request)
        # Limita por IP e por e-mail informado, para não permitir nem várias
        # tentativas vindas do mesmo lugar nem "bombardear" um único e-mail.
        rl_key = f"{ip}:{email.lower()}"

        result = ratelimit.check_rate_limit(
            "password_reset",
            rl_key,
            settings.PASSWORD_RESET_RATE_LIMIT_ATTEMPTS,
            settings.PASSWORD_RESET_RATE_LIMIT_WINDOW,
        )
        if result.blocked:
            messages.error(
                request,
                f"Muitas solicitações de redefinição de senha. Tente novamente em "
                f"{ratelimit.retry_after_minutes(result.retry_after)} minuto(s).",
            )
            return redirect("forgot-password")

        # Conta a tentativa já aqui (antes de saber se o e-mail existe), para não
        # permitir usar o rate limit para descobrir quais e-mails estão cadastrados.
        ratelimit.hit("password_reset", rl_key, settings.PASSWORD_RESET_RATE_LIMIT_WINDOW)

        # Gera o token e envia o e-mail SE o usuário existir (auth/services.py),
        # mas a resposta abaixo é a mesma nos dois casos: não confirma nem
        # nega a existência do e-mail (evita enumeração de contas).
        iniciar_redefinicao_de_senha(email)

        if settings.RESEND_API_KEY:
            messages.success(
                request,
                "Enviamos um e-mail com as instruções para redefinir sua senha. Verifique sua caixa de entrada.",
            )
        else:
            messages.error(
                request,
                "As configurações de e-mail não estão definidas. Não foi possível enviar o e-mail de redefinição de senha.",
            )

        return redirect("forgot-password")
