from django.urls import path

from web_project.decorators import screen_required
from .views import ProdutoView

urlpatterns = [
    path(
        "produto",
        screen_required("produto.view_produto")(ProdutoView.as_view(template_name="produto.html")),
        name="produto",
    ),
]
