from django.urls import path

from . import views

urlpatterns = [
    path("imagens/<str:tipo>/<int:pk>/enviar", views.enviar, name="imagens-enviar"),
    path("imagens/<str:tipo>/<int:pk>/excluir", views.excluir, name="imagens-excluir"),
    path("imagens/o/<str:token>", views.origem, name="imagens-origem"),
    path("imagens/<uuid:uuid>/<str:variante>", views.entrega, name="imagens-entrega"),
    # Painel do proprietário da plataforma (superusuário)
    path("plataforma/imagens", views.painel, name="imagens-painel"),
    path("plataforma/imagens/verificar", views.verificar, name="imagens-verificar"),
    path("plataforma/imagens/tarifas/<str:chave>", views.atualizar_tarifa, name="imagens-tarifa"),
]
