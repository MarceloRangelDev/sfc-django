"""Regras de autenticação compartilhadas entre as telas web e a API.

Mantém num único lugar o que não pode divergir entre os dois canais:
cadastro público, geração/validação do token de redefinição de senha e a
validação de força da senha. As views só traduzem o resultado para
mensagens (web) ou para JSON (API).
"""
import uuid
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from web_project.regras.validadores import validar_email

from .helpers import send_password_reset_email, send_verification_email
from .models import Profile

VALIDADE_TOKEN_REDEFINICAO = timedelta(hours=24)


class ErroDeAutenticacao(Exception):
    """Falha de regra de negócio de autenticação, com mensagem pronta para o
    usuário e (opcional) o campo a que se refere."""

    def __init__(self, mensagem, campo=None, conflito=False):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.campo = campo
        self.conflito = conflito  # duplicidade: a API responde 409


def validar_forca_da_senha(senha, user=None):
    """Aplica os AUTH_PASSWORD_VALIDATORS do projeto; devolve a lista de mensagens."""
    try:
        validate_password(senha, user=user)
    except ValidationError as erro:
        raise ErroDeAutenticacao(" ".join(erro.messages), "password") from None


def cadastrar_usuario_publico(username, email, password):
    """Cadastro público (autocadastro): cria o usuário no grupo de menor
    privilégio ("Visualizador"), SEM cliente (ou seja, sem acesso a módulos
    até um administrador vinculá-lo) e envia o e-mail de verificação.
    """
    username, email = (username or "").strip(), (email or "").strip()
    if not (username and email and password):
        raise ErroDeAutenticacao("Informe usuário, e-mail e senha.")
    if not validar_email(email):
        raise ErroDeAutenticacao("Informe um e-mail válido.", "email")
    if User.objects.filter(username=username, email=email).exists():
        raise ErroDeAutenticacao("Já existe um usuário com este nome de usuário e e-mail. Tente fazer login.", conflito=True)
    if User.objects.filter(email__iexact=email).exists() or Profile.objects.filter(email__iexact=email).exists():
        raise ErroDeAutenticacao("Este e-mail já está cadastrado.", "email", conflito=True)
    if User.objects.filter(username__iexact=username).exists():
        raise ErroDeAutenticacao("Este nome de usuário já está em uso.", "username", conflito=True)
    validar_forca_da_senha(password, user=User(username=username, email=email))

    token = str(uuid.uuid4())
    with transaction.atomic():
        usuario = User.objects.create_user(username=username, email=email, password=password)
        # "Visualizador" é o grupo ACL com menor privilégio (só visualizar).
        grupo, _ = Group.objects.get_or_create(name="Visualizador")
        usuario.groups.add(grupo)
        perfil, _ = Profile.objects.get_or_create(user=usuario)
        perfil.email_token = token
        perfil.email = email
        perfil.save()
    send_verification_email(email, token)
    return usuario


def iniciar_redefinicao_de_senha(email):
    """Gera o token (validade de 24 h) e envia o e-mail. Devolve True se o
    e-mail pertence a um usuário. O chamador NUNCA deve revelar esse
    resultado ao cliente (evita enumeração de contas)."""
    usuario = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if usuario is None:
        return False
    token = str(uuid.uuid4())
    perfil, _ = Profile.objects.get_or_create(user=usuario)
    perfil.forget_password_token = token
    perfil.forget_password_token_expiration = timezone.now() + VALIDADE_TOKEN_REDEFINICAO
    perfil.save()
    send_password_reset_email(usuario.email, token)
    return True


def perfil_por_token_de_redefinicao(token):
    """Profile dono do token, se existir e ainda não tiver expirado; senão None."""
    if not token:
        return None
    perfil = Profile.objects.filter(forget_password_token=token).select_related("user").first()
    if perfil is None or not perfil.forget_password_token_expiration:
        return None
    if perfil.forget_password_token_expiration < timezone.now():
        return None
    return perfil


def redefinir_senha(token, nova_senha):
    """Troca a senha usando o token (uso único). Devolve o usuário."""
    perfil = perfil_por_token_de_redefinicao(token)
    if perfil is None:
        raise ErroDeAutenticacao("Este link de redefinição de senha é inválido ou já expirou. Solicite um novo.", "token")
    validar_forca_da_senha(nova_senha, user=perfil.user)
    with transaction.atomic():
        perfil.user.set_password(nova_senha)
        perfil.user.save()
        perfil.forget_password_token = ""
        perfil.forget_password_token_expiration = None
        perfil.save()
    return perfil.user
