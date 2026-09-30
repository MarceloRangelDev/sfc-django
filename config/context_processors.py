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
