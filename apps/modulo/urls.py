from django.urls import path

from web_project.decorators import screen_required

from .views import ModuloClienteView

urlpatterns = [
    path(
        "modulos",
        screen_required("modulo.change_clientemodulo")(ModuloClienteView.as_view()),
        name="modulo-list",
    ),
]
