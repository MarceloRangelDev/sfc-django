from django.urls import path

from web_project.decorators import screen_required
from .views import PrecificacaoView

urlpatterns = [
    path(
        "precificacao",
        screen_required("precificacao.view_precificacao")(PrecificacaoView.as_view(template_name="precificacao.html")),
        name="precificacao",
    ),
]
