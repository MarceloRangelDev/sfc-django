"""Tag `static_v`: como `{% static %}`, mas acrescenta `?v=<data de modificação>` ao arquivo.

Os navegadores guardam em cache os arquivos .js; sem isso, depois de uma atualização do sistema a tela
continuaria usando a versão ANTIGA do script até o usuário forçar a recarga (Ctrl+F5). Com a versão na URL,
qualquer mudança no arquivo vira uma URL nova e é baixada sozinha. Fica disponível em todos os templates
(`builtins` em config/settings.py), sem precisar de `{% load %}`."""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(caminho):
    url = static(caminho)
    arquivo = finders.find(caminho)
    if isinstance(arquivo, str) and os.path.exists(arquivo):
        return f"{url}?v={int(os.path.getmtime(arquivo))}"
    return url  # arquivo fora dos finders (ex.: já coletado em produção): URL normal
