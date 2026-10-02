from django.db.models import Q
from django.utils.dateparse import parse_date

from .models import ACOES


def filtrar_registros(queryset, params):
    """Aplica os filtros da auditoria (pesquisa, usuário, módulo, ação e
    período) a um queryset. `params` é um dict/QueryDict (GET da tela ou da
    API) — valores inválidos são simplesmente ignorados."""
    termo = (params.get("q") or "").strip()
    if termo:
        queryset = queryset.filter(
            Q(descricao__icontains=termo) | Q(registro_nome__icontains=termo)
            | Q(registro_id__iexact=termo) | Q(usuario__username__icontains=termo)
        )
    if str(params.get("usuario", "")).isdigit():
        queryset = queryset.filter(usuario_id=params["usuario"])
    if params.get("modulo"):
        queryset = queryset.filter(modulo=params["modulo"])
    if params.get("acao") in ACOES:
        queryset = queryset.filter(acao=params["acao"])

    # Período: a data final é inclusiva (vai até o fim do dia).
    inicio, fim = parse_date(params.get("de") or ""), parse_date(params.get("ate") or "")
    if inicio:
        queryset = queryset.filter(criado_em__date__gte=inicio)
    if fim:
        queryset = queryset.filter(criado_em__date__lte=fim)
    return queryset.order_by("-criado_em", "-id")
