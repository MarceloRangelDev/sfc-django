from django.urls import path

from web_project.decorators import module_required
from .views import ProdutoView

urlpatterns = [
    path(
        "produto",
        module_required("produto", "produto.view_produto")(ProdutoView.as_view(template_name="produto.html")),
        name="produto",
    ),
]
