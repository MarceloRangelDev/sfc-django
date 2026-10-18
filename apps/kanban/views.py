"""Telas e ações JSON do Kanban (somente administrador; regra no serviço). Ações são POST com CSRF."""
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import TemplateView

from web_project import TemplateLayout
from web_project.regras import RegraNegocioError

from . import services


class KanbanView(TemplateView):
    template_name = "kanban.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        try:
            colunas = services.quadro(self.request.user)
            cliente = services.contexto(self.request.user)
        except RegraNegocioError as erro:
            context["erro_quadro"] = str(erro)
            return context
        context["quadro"] = [services.serializar(c) for c in colunas]
        context["usuarios"] = [{"id": u.pk, "nome": u.get_full_name() or u.username} for u in User.objects.filter(profile__cliente=cliente, is_active=True).order_by("first_name", "username")]
        return context


class AcaoView(View):
    """Base das ações: POST, devolve {success, message, data}; erro de regra => 400 (PermissionDenied/404 sobem como de costume)."""

    http_method_names = ["post"]

    def executar(self, request, **kwargs):  # pragma: no cover - implementado nas subclasses
        raise NotImplementedError

    def post(self, request, **kwargs):
        try:
            dados = self.executar(request, **kwargs)
        except RegraNegocioError as erro:
            return JsonResponse({"success": False, "message": str(erro)}, status=400)
        return JsonResponse({"success": True, "data": dados})


def _prazo(request):
    texto = request.POST.get("prazo", "").strip()
    if not texto:
        return None
    prazo = parse_date(texto)
    if prazo is None:
        raise RegraNegocioError("Prazo inválido.")
    return prazo


class ColunaCriar(AcaoView):
    def executar(self, request):
        return {"id": services.criar_coluna(request, request.POST.get("nome")).pk}


class ColunaRenomear(AcaoView):
    def executar(self, request, pk):
        return {"id": services.renomear_coluna(request, pk, request.POST.get("nome")).pk}


class ColunaExcluir(AcaoView):
    def executar(self, request, pk):
        services.excluir_coluna(request, pk)
        return {}


class CartaoCriar(AcaoView):
    def executar(self, request):
        c = services.criar_cartao(request, request.POST.get("coluna"), request.POST.get("titulo"), request.POST.get("descricao"), request.POST.get("responsavel"), _prazo(request))
        return {"id": c.pk}


class CartaoEditar(AcaoView):
    def executar(self, request, pk):
        c = services.editar_cartao(request, pk, request.POST.get("titulo"), request.POST.get("descricao"), request.POST.get("responsavel"), _prazo(request))
        return {"id": c.pk}


class CartaoExcluir(AcaoView):
    def executar(self, request, pk):
        services.excluir_cartao(request, pk)
        return {}


class CartaoMover(AcaoView):
    def executar(self, request, pk):
        c = services.mover_cartao(request, pk, request.POST.get("coluna"), request.POST.get("posicao"))
        return {"id": c.pk, "coluna": c.coluna_id, "ordem": c.ordem}
