"""Criação/edição de usuário: tudo numa transação e com auditoria.

Um usuário envolve várias tabelas (auth_user, accounts_profile, o grupo/perfil).
Gravar tudo dentro de `transaction.atomic()` garante que nunca fique um
usuário "pela metade" (ex.: criado sem cliente ou sem perfil).
"""
from django.db import transaction

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, tirar_foto
from auth.models import Profile

# Campos de auth_user que NÃO entram na auditoria: o hash da senha jamais
# deve ser gravado em log, e os demais são ruído (datas automáticas) ou
# privilégios que esta tela nem permite alterar.
CAMPOS_IGNORADOS = {"password", "last_login", "date_joined", "is_superuser", "is_staff", "groups", "user_permissions"}


def foto_usuario(usuario):
    """Foto de auditoria do usuário: campos do auth_user + cliente, perfil e
    segmento (que moram em outras tabelas)."""
    foto = tirar_foto(usuario, ignorar=CAMPOS_IGNORADOS)
    profile = usuario.profile
    grupo = usuario.groups.first()
    foto["cliente"] = {
        "rotulo": "Cliente", "valor": profile.cliente_id,
        "texto": str(profile.cliente) if profile.cliente_id else None,
    }
    foto["perfil"] = {"rotulo": "Perfil", "valor": grupo.pk if grupo else None, "texto": grupo.name if grupo else None}
    foto["segmento_trabalho"] = {
        "rotulo": "Segmento de trabalho", "valor": profile.segmento_trabalho,
        "texto": profile.get_segmento_trabalho_display(),
    }
    return foto


def salvar_usuario(request, form, antes=None):
    """Cria (antes=None) ou atualiza o usuário a partir de um UsuarioForm válido.

    `antes` é a foto (foto_usuario) tirada ANTES de o formulário ser
    construído: depois de form.is_valid() o objeto em memória já contém os
    valores novos, então a comparação precisa da foto anterior.
    """
    dados = form.cleaned_data
    criando = antes is None
    senha = dados.get("senha")

    with transaction.atomic():
        usuario = form.save(commit=False)
        # set_password usa o hashing nativo do Django; a senha nunca é
        # guardada em texto puro.
        if senha:
            usuario.set_password(senha)
        elif criando:
            usuario.set_unusable_password()
        usuario.save()

        # O Profile é criado automaticamente por um sinal ao criar o User.
        profile = usuario.profile if not criando else Profile.objects.get(user=usuario)
        profile.cliente = dados["cliente"]
        profile.segmento_trabalho = dados["segmento_trabalho"]
        profile.email = usuario.email  # mantém os dois e-mails iguais (Profile.email é único)
        profile.save()

        usuario.groups.set([dados["perfil"]])  # um usuário = um perfil
        usuario.refresh_from_db()

        if criando:
            registrar_criacao(request, usuario, modulo="usuario")
        else:
            alteracoes = comparar(antes, foto_usuario(usuario))
            if senha:
                alteracoes.append({"campo": "password", "rotulo": "Senha", "antes": "********", "depois": "alterada"})
            if alteracoes:
                registrar_alteracoes(request, usuario, alteracoes, modulo="usuario")
    return usuario


def alternar_ativo(request, usuario):
    """Ativa/inativa o usuário (nunca apaga: preserva o histórico e a auditoria)."""
    with transaction.atomic():
        antes = foto_usuario(usuario)
        usuario.is_active = not usuario.is_active
        usuario.save(update_fields=["is_active"])
        alteracoes = comparar(antes, foto_usuario(usuario))
        if alteracoes:
            registrar_alteracoes(request, usuario, alteracoes, modulo="usuario")
    return usuario
