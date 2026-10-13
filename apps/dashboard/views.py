from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import TemplateView

from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens

from .presenca import MINUTOS_ONLINE, usuarios_online
from .services import PRESETS, indicadores, periodo

PERMISSAO_VER_USUARIOS = "auth.view_user"


@method_decorator(login_required, name="dispatch")
class DashboardView(TemplateView):
    """Página inicial: indicadores da unidade escolhida, no período escolhido,
    só com o que o usuário tem permissão de ver."""

    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        usuario, params = self.request.user, self.request.GET
        unidades = list(clientes_dos_itens(usuario, "view").order_by("tipo_estabelecimento", "nome_razao_social"))
        cliente = getattr(usuario.profile, "cliente", None)
        unidade = next((u for u in unidades if str(u.pk) == params.get("unidade")), None) or cliente
        inicio, fim, preset, rotulo = periodo(params.get("periodo", "mes_atual"), parse_date(params.get("inicio", "")), parse_date(params.get("fim", "")))
        context.update(
            sem_unidade=unidade is None, unidades=unidades, mostrar_unidade=len(unidades) > 1, presets=PRESETS,
            preset=preset, inicio=inicio, fim=fim, rotulo_periodo=rotulo,
            unidade_atual=unidade, unidade_selecionada=str(unidade.pk) if unidade else "",
            pode_ver_online=usuario.has_perm(PERMISSAO_VER_USUARIOS), minutos_online=MINUTOS_ONLINE,
        )
        if unidade is not None:
            context["ind"] = indicadores(usuario, unidade, inicio, fim)
            context["tem_blocos"] = bool(context["ind"])
        if context["pode_ver_online"]:
            context["online_total"], _ = usuarios_online(usuario)
        return context


@method_decorator(login_required, name="dispatch")
class UsuariosOnlineView(View):
    """JSON com quem está online (nome, unidade e minutos desde a última atividade),
    restrito a quem tem `auth.view_user` e SEMPRE ao escopo do solicitante."""

    def get(self, request):
        if not request.user.has_perm(PERMISSAO_VER_USUARIOS):
            raise PermissionDenied
        total, lista = usuarios_online(request.user)
        return JsonResponse({"total": total, "limite_minutos": MINUTOS_ONLINE, "usuarios": lista})
