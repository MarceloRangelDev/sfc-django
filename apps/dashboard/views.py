import logging
import threading

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import TemplateView

from apps.acl.interface import menu_lateral_oculto
from apps.integracao.models import Conexao
from apps.integracao.services import sincronizar_automatico
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens, modulos_habilitados

from .presenca import MINUTOS_ONLINE, usuarios_online
from .services import PRESETS, indicadores, periodo

logger = logging.getLogger(__name__)
PERMISSAO_VER_USUARIOS = "auth.view_user"


@method_decorator(login_required, name="dispatch")
class DashboardView(TemplateView):
    """Página inicial: indicadores da unidade escolhida, no período escolhido,
    só com o que o usuário tem permissão de ver."""

    template_name = "dashboard.html"

    def get(self, request, *args, **kwargs):
        # Papel sem menu lateral (ex.: Caixa) e sem o painel: a "tela principal" é a primeira tela do PDV que ele pode usar.
        if menu_lateral_oculto(request.user) and not request.user.has_perm("dashboard.view_painel"):
            for permissao, rota in (("pdv.view_telavender", "pdv"), ("pdv.view_telameucaixa", "pdv-caixa"), ("pdv.view_telacomandas", "pdv-comandas")):
                if request.user.has_perm(permissao):
                    return redirect(rota)
        self._sincronizar_agenda_em_segundo_plano(request.user)
        return super().get(request, *args, **kwargs)

    @staticmethod
    def _sincronizar_agenda_em_segundo_plano(usuario):
        """Ao abrir o sistema, sincroniza as agendas da unidade (máx. a cada 5 min) numa thread: a página não espera o provedor externo."""
        cliente = getattr(getattr(usuario, "profile", None), "cliente", None)
        if cliente is None or not usuario.has_perm("integracao.view_compromisso"):
            return
        if not Conexao.objects.filter(cliente=cliente, status=Conexao.CONECTADA).exists():
            return  # nada conectado: não abre thread

        def rodar():
            try:
                sincronizar_automatico(cliente)
            except Exception:
                logger.exception("Sincronização da agenda ao abrir o sistema falhou")
            finally:
                connection.close()  # a thread tem a própria conexão com o banco

        threading.Thread(target=rodar, daemon=True).start()

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        usuario, params = self.request.user, self.request.GET
        unidades = list(clientes_dos_itens(usuario, "view").order_by("tipo_estabelecimento", "nome_razao_social"))
        cliente = getattr(usuario.profile, "cliente", None)
        unidade = next((u for u in unidades if str(u.pk) == params.get("unidade")), None) or cliente
        inicio, fim, preset, rotulo = periodo(params.get("periodo", "mes_atual"), parse_date(params.get("inicio", "")), parse_date(params.get("fim", "")))
        # Painel é um módulo (Módulos, Papéis e Permissões especiais): sem o módulo habilitado ou a permissão, a página inicial abre sem indicadores.
        pode_painel = usuario.has_perm("dashboard.view_painel") and "painel" in modulos_habilitados(usuario)
        context.update(
            sem_painel=not pode_painel, sem_unidade=unidade is None, unidades=unidades, mostrar_unidade=len(unidades) > 1, presets=PRESETS,
            preset=preset, inicio=inicio, fim=fim, rotulo_periodo=rotulo,
            unidade_atual=unidade, unidade_selecionada=str(unidade.pk) if unidade else "",
            pode_ver_online=usuario.has_perm(PERMISSAO_VER_USUARIOS), minutos_online=MINUTOS_ONLINE,
        )
        if unidade is not None and pode_painel:
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
