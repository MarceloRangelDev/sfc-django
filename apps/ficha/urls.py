from django.urls import path

from web_project.decorators import module_required

from web_project.documento import DocumentoView

from .models import Ficha
from .views import FichaCalcularView, FichaCreateView, FichaExcluirView, FichaListView, FichaUpdateView

urlpatterns = [
    path("ficha/<int:pk>/exportar", module_required("ficha", "ficha.view_ficha")(DocumentoView.as_view(modelo=Ficha)), name="ficha-exportar"),
    path("ficha", module_required("ficha", "ficha.view_ficha")(FichaListView.as_view()), name="ficha"),
    path("ficha/novo", module_required("ficha", "ficha.add_ficha")(FichaCreateView.as_view()), name="ficha-create"),
    # Prévia de custos do formulário (qualquer usuário que veja fichas; só lê e calcula).
    path("ficha/calcular", module_required("ficha", "ficha.view_ficha")(FichaCalcularView.as_view()), name="ficha-calcular"),
    path("ficha/<int:pk>/editar", module_required("ficha", "ficha.change_ficha")(FichaUpdateView.as_view()), name="ficha-edit"),
    path("ficha/<int:pk>/excluir", module_required("ficha", "ficha.delete_ficha")(FichaExcluirView.as_view()), name="ficha-excluir"),
]
