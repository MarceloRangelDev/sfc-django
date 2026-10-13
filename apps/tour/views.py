from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import TourPreferencia


@login_required
@require_GET
def estado(request, pagina):
    """Informa se o usuário pediu para não ver mais o tour desta página."""
    ocultar = TourPreferencia.objects.filter(usuario=request.user, pagina=pagina).exists()
    return JsonResponse({"ocultar": ocultar})


@login_required
@require_POST
def ocultar(request, pagina):
    """Grava "Não mostrar novamente" (sempre para o próprio usuário logado)."""
    TourPreferencia.objects.get_or_create(usuario=request.user, pagina=pagina)
    return JsonResponse({"ocultar": True})
