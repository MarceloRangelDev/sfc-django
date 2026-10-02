from django.urls import path

from web_project.decorators import screen_required

from .views import MedidaAlternarAtivoView, MedidaCreateView, MedidaExcluirView, MedidaListView, MedidaUpdateView

# Medidas é um cadastro GLOBAL e administrativo: usa screen_required (login
# + permissão), não module_required (que é por cliente). As permissões
# medida.* são concedidas só ao grupo Administrador (ver auth/management/
# commands/seed_acl.py).
urlpatterns = [
    path("medidas", screen_required("medida.view_medida")(MedidaListView.as_view()), name="medida-list"),
    path("medidas/novo", screen_required("medida.add_medida")(MedidaCreateView.as_view()), name="medida-create"),
    path("medidas/<int:pk>/editar", screen_required("medida.change_medida")(MedidaUpdateView.as_view()), name="medida-edit"),
    path(
        "medidas/<int:pk>/alternar-ativo",
        screen_required("medida.change_medida")(MedidaAlternarAtivoView.as_view()),
        name="medida-alternar-ativo",
    ),
    path("medidas/<int:pk>/excluir", screen_required("medida.delete_medida")(MedidaExcluirView.as_view()), name="medida-excluir"),
]
