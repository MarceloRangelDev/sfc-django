from datetime import timedelta

from django.contrib.auth import get_user_model
from django.views.generic import DetailView, ListView

from web_project import TemplateLayout
from web_project.acesso import administrador_global, cliente_do_usuario

from .filtros import filtrar_registros
from .models import ACOES, RegistroAuditoria

PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]


def registros_visiveis(user):
    """Quais registros o usuário pode consultar.

    Administrador global (superusuário ou grupo "Administrador", que também
    gerencia Clientes) vê tudo. Qualquer outro usuário com a permissão
    `view_registroauditoria` só enxerga os registros do PRÓPRIO Cliente —
    a auditoria de um cliente nunca vaza para outro.
    """
    queryset = RegistroAuditoria.objects.select_related("usuario", "cliente")
    if administrador_global(user):
        return queryset
    cliente = cliente_do_usuario(user)
    return queryset.filter(cliente=cliente) if cliente else queryset.none()


class AuditoriaListView(ListView):
    """Lista paginada com pesquisa e filtros por usuário, módulo, ação e período."""

    template_name = "auditoria_list.html"
    context_object_name = "registros"

    def get_queryset(self):
        return filtrar_registros(registros_visiveis(self.request.user), self.request.GET)

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
