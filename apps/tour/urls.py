from django.urls import path

from . import views

urlpatterns = [
    path("tour/<slug:pagina>/", views.estado, name="tour-estado"),
    path("tour/<slug:pagina>/ocultar", views.ocultar, name="tour-ocultar"),
]
