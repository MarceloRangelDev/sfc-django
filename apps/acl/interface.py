"""Regras de interface por papel: menu lateral oculto (modo caixa)."""


def menu_lateral_oculto(user):
    """True quando o usuário tem papéis e TODOS eles ocultam o menu lateral. Superusuário nunca (precisa navegar). Cache por requisição."""
    if not getattr(user, "is_authenticated", False) or user.is_superuser:
        return False
    if not hasattr(user, "_menu_lateral_oculto"):
        papeis = user.groups.all()
        user._menu_lateral_oculto = papeis.exists() and not papeis.exclude(config_menu__ocultar_menu_lateral=True).exists()
    return user._menu_lateral_oculto
