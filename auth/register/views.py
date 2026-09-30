from django.shortcuts import redirect
from django.contrib.auth.models import User, Group
from django.contrib import messages
from django.conf import settings
from auth.views import AuthView
from auth.helpers import send_verification_email
from auth.models import Profile
import uuid


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

        # Verifica duplicidade antes de criar o usuário.
        if User.objects.filter(username=username, email=email).exists():
            messages.error(request, "Já existe um usuário com este nome de usuário e e-mail. Tente fazer login.")
            return redirect("register")
        elif User.objects.filter(email=email).exists():
            messages.error(request, "Este e-mail já está cadastrado.")
            return redirect("register")
        elif User.objects.filter(username=username).exists():
            messages.error(request, "Este nome de usuário já está em uso.")
            return redirect("register")

        created_user = User.objects.create_user(username=username, email=email, password=password)
        created_user.set_password(password)
        created_user.save()

        # Grupo padrão para autocadastro: "Visualizador" é o grupo ACL com
        # menor privilégio (só permissão de visualizar, ver seed_acl.py).
        # Antes o usuário era colocado num grupo "client" separado que não
        # tinha nenhuma permissão atribuída — na prática, ficava sem acesso
        # a nenhuma tela até um administrador reatribuir o grupo manualmente.
        user_group, _created = Group.objects.get_or_create(name="Visualizador")
        created_user.groups.add(user_group)

        # Gera o token de verificação de e-mail e envia o e-mail de confirmação.
        token = str(uuid.uuid4())

        user_profile, _created = Profile.objects.get_or_create(user=created_user)
        user_profile.email_token = token
        user_profile.email = email
        user_profile.save()

        send_verification_email(email, token)

        if settings.RESEND_API_KEY:
            messages.success(request, "E-mail de verificação enviado com sucesso.")
        else:
            messages.error(
                request,
                "As configurações de e-mail não estão definidas. Não foi possível enviar o e-mail de verificação.",
            )

        request.session['email'] = email  # Guarda o e-mail na sessão para a tela de verificação.
        return redirect("verify-email-page")
