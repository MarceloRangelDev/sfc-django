from django.urls import path

from web_project.decorators import module_required

from .views import (
    ModuloClienteView,
    ModuloCreateView,
    ModuloExcluirView,
    ModuloListView,
    ModuloOrdemView,
    ModuloUpdateView,
    SegmentoCreateView,
    SegmentoExcluirView,
    SegmentoListView,
    SegmentoUpdateView,
)

urlpatterns = [
    path("modulos", module_required("modulo-cliente", "modulo.change_clientemodulo")(ModuloClienteView.as_view()), name="modulo-list"),
    path("modulos/ordem", module_required("modulo-cliente", "modulo.change_clientemodulo")(ModuloOrdemView.as_view()), name="modulo-ordem"),
    path("modulos/catalogo", module_required("modulo", "modulo.view_modulo")(ModuloListView.as_view()), name="modulo-catalogo"),
    path("modulos/catalogo/novo", module_required("modulo", "modulo.add_modulo")(ModuloCreateView.as_view()), name="modulo-create"),
    path("modulos/catalogo/<int:pk>/editar", module_required("modulo", "modulo.change_modulo")(ModuloUpdateView.as_view()), name="modulo-edit"),
    path("modulos/catalogo/<int:pk>/excluir", module_required("modulo", "modulo.delete_modulo")(ModuloExcluirView.as_view()), name="modulo-excluir"),
    path("segmentos", module_required("segmento", "modulo.view_segmento")(SegmentoListView.as_view()), name="segmento-list"),
    path("segmentos/novo", module_required("segmento", "modulo.add_segmento")(SegmentoCreateView.as_view()), name="segmento-create"),
    path("segmentos/<int:pk>/editar", module_required("segmento", "modulo.change_segmento")(SegmentoUpdateView.as_view()), name="segmento-edit"),
    path("segmentos/<int:pk>/excluir", module_required("segmento", "modulo.delete_segmento")(SegmentoExcluirView.as_view()), name="segmento-excluir"),
]
