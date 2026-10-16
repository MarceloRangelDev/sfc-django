from django.urls import path

from web_project.decorators import module_required

from .views import ProdutoCodigoImagemView, ProdutoCreateView, ProdutoEtiquetasView, ProdutoExcluirView, ProdutoListView, ProdutoUpdateView

urlpatterns = [
    path("produto", module_required("produto", "produto.view_produto")(ProdutoListView.as_view()), name="produto"),
    path("produto/novo", module_required("produto", "produto.add_produto")(ProdutoCreateView.as_view()), name="produto-create"),
    path(
        "produto/<int:pk>/editar",
        module_required("produto", "produto.change_produto")(ProdutoUpdateView.as_view()),
        name="produto-edit",
    ),
    path(
        "produto/<int:pk>/excluir",
        module_required("produto", "produto.delete_produto")(ProdutoExcluirView.as_view()),
        name="produto-excluir",
    ),
    path("produto/etiquetas", module_required("produto", "produto.view_produto")(ProdutoEtiquetasView.as_view()), name="produto-etiquetas"),
    path(
        "produto/<int:pk>/codigo/<str:tipo>.svg",
        module_required("produto", "produto.view_produto")(ProdutoCodigoImagemView.as_view()),
        name="produto-codigo",
    ),
]
