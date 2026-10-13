"""Criação de Cliente com usuário Gestor padrão, tudo numa transação."""
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.acl.padroes import aplicar_padrao_aos_modulos
from apps.auditoria.utils import registrar_criacao
from apps.modulo.models import ClienteModulo
from web_project.acesso import PERFIS_PADRAO

PERFIL_GESTOR = "Gestor"


def _username_unico(nome, documento):
    """Usuário com o nome do cliente; se já existir, acrescenta o documento."""
    candidato = nome[:150]
    if User.objects.filter(username__iexact=candidato).exists():
        candidato = f"{nome[:150 - len(documento) - 1]} {documento}"
    return candidato


def criar_cliente(request, cliente, perfis, email_gestor):
    """Salva o cliente (ainda não gravado) e cria o usuário Gestor padrão.
    Usado pela tela web e pela API.

    Se qualquer passo falhar (ex.: perfil Gestor inexistente), nada é gravado:
    o cliente nunca fica sem o seu primeiro usuário.
    """
    with transaction.atomic():
        cliente.save()
        cliente.perfis.set(perfis)

        gestor = Group.objects.filter(name=PERFIL_GESTOR).first()
        if gestor is None:
            raise ValidationError("O perfil Gestor não existe. Rode 'python manage.py seed_acl'.")
        # Gestor e Operador sempre ficam disponíveis para o cliente novo.
        cliente.perfis.add(*Group.objects.filter(name__in=PERFIS_PADRAO))

        # Módulos do segmento já nascem habilitados (o Admin pode desligar depois).
        if cliente.segmento_id:
            ClienteModulo.objects.bulk_create(
                [ClienteModulo(cliente=cliente, modulo=m, ativo=True) for m in cliente.segmento.modulos.all()],
                ignore_conflicts=True,
            )
            # Módulos habilitados já nascem EFETIVOS para os usuários do cliente (perfis padrão).
            aplicar_padrao_aos_modulos(cliente.segmento.modulos.filter(essencial=False))

        usuario = User.objects.create_user(
            username=_username_unico(cliente.nome_razao_social, cliente.cpf_cnpj),
            email=email_gestor,
            first_name=cliente.nome_razao_social[:150],
        )
        usuario.set_unusable_password()  # a senha é definida pelo fluxo "Esqueci minha senha"
        usuario.save(update_fields=["password"])
        usuario.profile.cliente = cliente
        usuario.profile.save(update_fields=["cliente"])
        usuario.groups.set([gestor])

        registrar_criacao(request, cliente)
        registrar_criacao(request, usuario, modulo="usuario")
    return cliente
