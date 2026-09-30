from django.urls import path

from web_project.decorators import module_required
from .views import FichaView

urlpatterns = [
    path(
        "ficha",
        module_required("ficha", "ficha.view_ficha")(FichaView.as_view(template_name="ficha.html")),
        name="ficha",
    ),
]
