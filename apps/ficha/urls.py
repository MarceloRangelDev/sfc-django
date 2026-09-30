from django.urls import path

from web_project.decorators import screen_required
from .views import FichaView

urlpatterns = [
    path(
        "ficha",
        screen_required("ficha.view_ficha")(FichaView.as_view(template_name="ficha.html")),
        name="ficha",
    ),
]
