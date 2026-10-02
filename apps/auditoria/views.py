from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.views.generic import DetailView, ListView

from web_project import TemplateLayout

from .models import ACOES, RegistroAuditoria

PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]
GRUPO_ADMINISTRADOR = "Administrador"


def registros_visiveis(user):
    """Quais registros o usuário pode consultar.

    Administrador global (superusuário ou grupo "Administrador", que também
    gerencia Clientes) vê tudo. Qualquer outro usuário com a permissão
    `view_registroauditoria` só enxerga os registros do PRÓPRIO Cliente —
    a auditoria de um cliente nunca vaza para outro.
    """
    queryset = RegistroAuditoria.objects.select_related("usuario", "cliente")
    if user.is_superuser or user.groups.filter(name=GRUPO_ADMINISTRADOR).exists():
        return queryset
    cliente = getattr(getattr(user, "profile", None), "cliente", None)
    return queryset.filter(cliente=cliente) if cliente else queryset.none()


class AuditoriaListView(ListView):
    """Lista paginada com pesquisa e filtros por usuário, módulo, ação e período."""

    template_name = "auditoria_list.html"
    context_object_name = "registros"

    def get_queryset(self):
        queryset = registros_visiveis(self.request.user)
        params = self.request.GET

        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(
                Q(descricao__icontains=termo) | Q(registro_nome__icontains=termo)
                | Q(registro_id__iexact=termo) | Q(usuario__username__icontains=termo)
            )
        if params.get("usuario", "").isdigit():
            queryset = queryset.filter(usuario_id=params["usuario"])
        if params.get("modulo"):
            queryset = queryset.filter(modulo=params["modulo"])
        if params.get("acao") in ACOES:
            queryset = queryset.filter(acao=params["acao"])

        # Período: a data final é inclusiva (vai até o fim do dia).
        inicio, fim = parse_date(params.get("de", "")), parse_date(params.get("ate", ""))
        if inicio:
            queryset = queryset.filter(criado_em__date__gte=inicio)
        if fim:
            queryset = queryset.filter(criado_em__date__lte=fim)
        return queryset.order_by("-criado_em", "-id")

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", PADRAO_POR_PAGINA))
        except (TypeError, ValueError):
            valor = PADRAO_POR_PAGINA
        return valor if valor in OPCOES_POR_PAGINA else PADRAO_POR_PAGINA

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        base = registros_visiveis(self.request.user)
        usuarios_ids = base.exclude(usuario__isnull=True).values_list("usuario_id", flat=True).distinct()
        context.update(
            {
                "filtros": self.request.GET,
                "acoes": ACOES,
                "modulos": sorted(set(base.exclude(modulo="").values_list("modulo", flat=True))),
                "usuarios": get_user_model().objects.filter(pk__in=usuarios_ids).order_by("username"),
                "per_page": self.get_paginate_by(None),
                "opcoes_por_pagina": OPCOES_POR_PAGINA,
            }
        )
        return context


class AuditoriaDetailView(DetailView):
    template_name = "auditoria_detail.html"
    context_object_name = "registro"

    def get_queryset(self):
        return registros_visiveis(self.request.user)  # registro de outro cliente -> 404

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["acao_rotulo"] = self.object.acao_rotulo
        return context
