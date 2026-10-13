from django.urls import path

from .views import UsuariosOnlineView

# O painel é a rota "index" (apps/sample/urls.py). Aqui só a consulta de usuários online.
urlpatterns = [
    path("painel/online", UsuariosOnlineView.as_view(), name="dashboard-online"),
]
