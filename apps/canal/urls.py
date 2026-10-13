from django.urls import path

from web_project.decorators import module_required

from .views import CanalCreateView, CanalListView, CanalUpdateView

# Módulo "canal-venda" (Configurações, essencial: só depende da permissão do perfil).
urlpatterns = [
    path("canais-venda", module_required("canal-venda", "canal.view_canalvenda")(CanalListView.as_view()), name="canal-list"),
    path("canais-venda/novo", module_required("canal-venda", "canal.add_canalvenda")(CanalCreateView.as_view()), name="canal-create"),
    path(
        "canais-venda/<int:pk>/editar",
        module_required("canal-venda", "canal.change_canalvenda")(CanalUpdateView.as_view()),
        name="canal-edit",
    ),
]
