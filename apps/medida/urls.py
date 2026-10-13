from django.contrib.auth.decorators import login_required
from django.urls import path

from web_project.decorators import module_required

from .views import MedidaAlternarAtivoView, MedidaCalcularView, MedidaCreateView, MedidaExcluirView, MedidaListView, MedidaUpdateView

# Medidas é um cadastro GLOBAL e administrativo: o módulo "medida" é essencial
# (só depende da permissão). As permissões medida.* são concedidas só ao grupo
# Administrador (ver auth/management/commands/seed_acl.py).
urlpatterns = [
    # Prévia de cálculos para formulários (qualquer usuário logado).
    path("medidas/calcular", login_required(MedidaCalcularView.as_view()), name="medida-calcular"),
    path("medidas", module_required("medida", "medida.view_medida")(MedidaListView.as_view()), name="medida-list"),
    path("medidas/novo", module_required("medida", "medida.add_medida")(MedidaCreateView.as_view()), name="medida-create"),
    path("medidas/<int:pk>/editar", module_required("medida", "medida.change_medida")(MedidaUpdateView.as_view()), name="medida-edit"),
    path(
        "medidas/<int:pk>/alternar-ativo",
        module_required("medida", "medida.change_medida")(MedidaAlternarAtivoView.as_view()),
        name="medida-alternar-ativo",
    ),
    path("medidas/<int:pk>/excluir", module_required("medida", "medida.delete_medida")(MedidaExcluirView.as_view()), name="medida-excluir"),
]
