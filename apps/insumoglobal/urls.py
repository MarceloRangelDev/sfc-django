from django.urls import path

from web_project.decorators import module_required

from . import views

G, N = "insumo-global", "insumo-nao-conformes"

urlpatterns = [
    path("insumos/global", module_required(G, "insumoglobal.view_insumoglobal")(views.InsumoGlobalListView.as_view()), name="insumo-global"),
    path("insumos/global/novo", module_required(G, "insumoglobal.add_insumoglobal")(views.InsumoGlobalCreateView.as_view()), name="insumo-global-create"),
    path("insumos/global/<int:pk>/editar", module_required(G, "insumoglobal.change_insumoglobal")(views.InsumoGlobalUpdateView.as_view()), name="insumo-global-edit"),
    path("insumos/global/<int:pk>/alternar", module_required(G, "insumoglobal.change_insumoglobal")(views.InsumoGlobalAlternarView.as_view()), name="insumo-global-alternar"),
    path("insumos/nao-conformes", module_required(N, "insumoglobal.view_naoconformes")(views.NaoConformesView.as_view()), name="insumo-nao-conformes"),
    path("insumos/nao-conformes/<int:pk>/promover", module_required(N, "insumoglobal.padronizar_insumo")(views.PromoverView.as_view()), name="insumo-nao-conforme-promover"),
    path("insumos/nao-conformes/<int:pk>/redirecionar", module_required(N, "insumoglobal.padronizar_insumo")(views.RedirecionarView.as_view()), name="insumo-nao-conforme-redirecionar"),
]
