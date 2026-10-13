from django.urls import path

from web_project.decorators import module_required

from .views import (
    PrecificacaoCalcularView,
    PrecificacaoCreateView,
    PrecificacaoExcluirView,
    PrecificacaoListView,
    PrecificacaoUpdateView,
)

urlpatterns = [
    path("precificacao", module_required("precificacao", "precificacao.view_precificacao")(PrecificacaoListView.as_view()), name="precificacao"),
    path("precificacao/novo", module_required("precificacao", "precificacao.add_precificacao")(PrecificacaoCreateView.as_view()), name="precificacao-create"),
    # Prévia do formulário: só lê e calcula (quem vê precificações pode usar).
    path("precificacao/calcular", module_required("precificacao", "precificacao.view_precificacao")(PrecificacaoCalcularView.as_view()), name="precificacao-calcular"),
    path("precificacao/<int:pk>/editar", module_required("precificacao", "precificacao.change_precificacao")(PrecificacaoUpdateView.as_view()), name="precificacao-edit"),
    path("precificacao/<int:pk>/excluir", module_required("precificacao", "precificacao.delete_precificacao")(PrecificacaoExcluirView.as_view()), name="precificacao-excluir"),
]
