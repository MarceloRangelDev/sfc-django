from django.urls import path

from web_project.decorators import module_required
from .views import PrecificacaoView

urlpatterns = [
    path(
        "precificacao",
        module_required("precificacao", "precificacao.view_precificacao")(PrecificacaoView.as_view(template_name="precificacao.html")),
        name="precificacao",
    ),
]
