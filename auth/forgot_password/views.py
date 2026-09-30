from django.shortcuts import redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from auth.helpers import send_password_reset_email
from auth.models import Profile  # Import the Profile model
from auth.views import AuthView
from auth import ratelimit
from datetime import timedelta, datetime
import uuid


class ForgetPasswordView(AuthView):
    def get(self, request):
        if request.user.is_authenticated:
            # If the user is already logged in, redirect them to the home page or another appropriate page.
            return redirect("index")  # Replace 'index' with the actual URL name for the home page

        # Render the login page for users who are not logged in.
        return super().get(request)

    def post(self, request):
        if request.method == "POST":
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

            user = User.objects.filter(email=email).first()
            if not user:
                messages.error(request, "No user with this email exists.")
                return redirect("forgot-password")

            # Generate a token and send a password reset email here
            token = str(uuid.uuid4())

            # Set the token in the user's profile and add an expiration time (e.g., 24 hours from now)
            expiration_time = datetime.now() + timedelta(hours=24)

            user_profile, created = Profile.objects.get_or_create(user=user)
            user_profile.forget_password_token = token
            user_profile.forget_password_token_expiration = expiration_time
            user_profile.save()

            # Send the password reset email
            send_password_reset_email(email, token)

            if settings.RESEND_API_KEY:
                messages.success(request, "A password reset email has been sent. Please check your inbox")
            else:
                messages.error(request, "Email settings are not configured. Unable to send verification email.")


            return redirect("forgot-password")
