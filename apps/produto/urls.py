from django.urls import path

from web_project.decorators import module_required

from .views import ProdutoCreateView, ProdutoExcluirView, ProdutoListView, ProdutoUpdateView

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
]
