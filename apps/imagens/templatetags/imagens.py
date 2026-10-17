from django import template

from apps.imagens import servico

register = template.Library()


@register.simple_tag
def imagem_url(tipo, entidade_id, variante="miniatura"):
    """URL da imagem atual da entidade ('' se não houver). Ex.: {% imagem_url 'produto' p.pk 'miniatura' %}.
    A URL passa pela autorização a cada acesso; use em listas paginadas (1 consulta por linha)."""
    return servico.url_da_entidade(tipo, entidade_id, variante)
