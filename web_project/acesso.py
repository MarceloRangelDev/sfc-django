"""Regras de escopo de acesso compartilhadas entre telas administrativas."""

GRUPO_ADMINISTRADOR = "Administrador"


def administrador_global(user):
    """True para superusuário ou membro do grupo "Administrador": quem
    administra o sistema inteiro (todos os Clientes). Qualquer outro usuário
    enxerga/gerencia apenas os dados do PRÓPRIO Cliente."""
    return bool(user.is_authenticated and (user.is_superuser or user.groups.filter(name=GRUPO_ADMINISTRADOR).exists()))


def cliente_do_usuario(user):
    return getattr(getattr(user, "profile", None), "cliente", None)
