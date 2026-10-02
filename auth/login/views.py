from django.conf import settings
from django.shortcuts import redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib import messages
from auth.views import AuthView
from auth import ratelimit


class LoginView(AuthView):
    def get(self, request):
        if request.user.is_authenticated:
            # If the user is already logged in, redirect them to the home page or another appropriate page.
            return redirect("index")  # Replace 'index' with the actual URL name for the home page

        # Render the login page for users who are not logged in.
        return super().get(request)

    def post(self, request):
        if request.method == "POST":
            username = request.POST.get("email-username")
            password = request.POST.get("password")

            if not (username and password):
                messages.error(request, "Por favor, insira seu nome de usuário e senha.")
                return redirect("login")

            ip = ratelimit.get_client_ip(request)
            # Chave combinando IP + identificador informado: limita tanto tentativas
            # vindas do mesmo IP quanto contra a mesma conta.
            rl_key = f"{ip}:{username.lower()}"

            result = ratelimit.check_rate_limit(
                "login",
                rl_key,
                settings.LOGIN_RATE_LIMIT_ATTEMPTS,
                settings.LOGIN_RATE_LIMIT_WINDOW,
            )
            if result.blocked:
                messages.error(
                    request,
                    f"Muitas tentativas de login. Tente novamente em "
                    f"{ratelimit.retry_after_minutes(result.retry_after)} minuto(s).",
                )
                return redirect("login")

            if "@" in username:
                user_email = User.objects.filter(email=username).first()
                if user_email is None:
                    ratelimit.hit("login", rl_key, settings.LOGIN_RATE_LIMIT_WINDOW)
                    # Mesma mensagem de "senha errada": não revela se a conta existe.
                    messages.error(request, "Nome de usuário ou senha inválidos.")
                    return redirect("login")
                username = user_email.username

            user_email = User.objects.filter(username=username).first()
            if user_email is None:
                ratelimit.hit("login", rl_key, settings.LOGIN_RATE_LIMIT_WINDOW)
                messages.error(request, "Nome de usuário ou senha inválidos.")
                return redirect("login")

            authenticated_user = authenticate(request, username=username, password=password)
            if authenticated_user is not None:
                # Login bem-sucedido: limpa o contador de tentativas
                ratelimit.reset("login", rl_key)

                # Login the user if authentication is successful
                login(request, authenticated_user)

                # Redirect to the page the user was trying to access before logging in
                if "next" in request.POST:
                    return redirect(request.POST["next"])
                else: # Redirect to the home page or another appropriate page
                    return redirect("index")
            else:
                ratelimit.hit("login", rl_key, settings.LOGIN_RATE_LIMIT_WINDOW)
                messages.error(request, "Nome de usuário ou senha inválidos.")
                return redirect("login")
