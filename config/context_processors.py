from django.conf import settings

def my_setting(request):
    return {'MY_SETTING': settings}

def language_code(request):
    return {"LANGUAGE_CODE": request.LANGUAGE_CODE}

def get_cookie(request):
    return {"COOKIES": request.COOKIES}

# Add the 'ENVIRONMENT' setting to the template context
def environment(request):
    return {'ENVIRONMENT': settings.ENVIRONMENT}


def cliente_atual(request):
    """Disponibiliza o Cliente (empresa/tenant) do usuário logado em todos
    os templates, como `cliente_atual` — usado, por exemplo, para exibir o
    nome da empresa no layout e, a partir da Fase 3, para checar módulos
    habilitados no menu."""
    cliente = None
    if request.user.is_authenticated:
        cliente = getattr(request.user.profile, "cliente", None)
    return {"cliente_atual": cliente}


def menu_modulos(request):
    """Itens de menu vindos da tabela de módulos (ver web_project/menu.py)."""
    from web_project.menu import montar_menu

    return {"menu_modulos": montar_menu(request.user)}


def notificacoes_navbar(request):
    """Contador e últimas notificações do usuário para o sino da navbar."""
    if not request.user.is_authenticated:
        return {}
    from apps.notificacao.services import resumo_do_usuario

    nao_lidas, recentes = resumo_do_usuario(request.user)
    return {
        "notificacoes_nao_lidas": nao_lidas,
        "notificacoes_recentes": recentes,
        "push_habilitado": bool(settings.WEBPUSH_SETTINGS["VAPID_PUBLIC_KEY"]),
        "vapid_public_key": settings.WEBPUSH_SETTINGS["VAPID_PUBLIC_KEY"],
    }
