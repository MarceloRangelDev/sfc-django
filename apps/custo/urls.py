from django.urls import path

from web_project.decorators import module_required

from .views import (
    CustoCategoriaAjusteView,
    CustoCategoriasView,
    CustoComumCreateView,
    CustoComumExcluirView,
    CustoComumListView,
    CustoComumUpdateView,
    CustoCreateView,
    CustoExcluirView,
    CustoListView,
    CustoUpdateView,
)

urlpatterns = [
    path("custo-operacional", module_required("custo-operacional", "custo.view_custooperacional")(CustoListView.as_view()), name="custo-list"),
    path("custo-operacional/novo", module_required("custo-operacional", "custo.add_custooperacional")(CustoCreateView.as_view()), name="custo-create"),
    path("custo-operacional/categorias", module_required("custo-operacional", "custo.view_custooperacional")(CustoCategoriasView.as_view()), name="custo-categorias"),
    path(
        "custo-operacional/categorias/<int:pk>/ajustar",
        module_required("custo-operacional", "custo.change_custooperacional")(CustoCategoriaAjusteView.as_view()),
        name="custo-categoria-ajustar",
    ),
    # Configurações > Custos comuns (módulo "custo-comum": essencial, só depende da permissão do perfil)
    path("custos-comuns", module_required("custo-comum", "custo.view_customodelo")(CustoComumListView.as_view()), name="custo-comum-list"),
    path("custos-comuns/novo", module_required("custo-comum", "custo.add_customodelo")(CustoComumCreateView.as_view()), name="custo-comum-create"),
    path("custos-comuns/<int:pk>/editar", module_required("custo-comum", "custo.change_customodelo")(CustoComumUpdateView.as_view()), name="custo-comum-edit"),
    path("custos-comuns/<int:pk>/excluir", module_required("custo-comum", "custo.delete_customodelo")(CustoComumExcluirView.as_view()), name="custo-comum-excluir"),
    path("custo-operacional/<int:pk>/editar", module_required("custo-operacional", "custo.change_custooperacional")(CustoUpdateView.as_view()), name="custo-edit"),
    path("custo-operacional/<int:pk>/excluir", module_required("custo-operacional", "custo.delete_custooperacional")(CustoExcluirView.as_view()), name="custo-excluir"),
]
