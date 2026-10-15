from django.urls import path

from web_project.decorators import module_required

from . import views

M = "pedido"
ver = module_required(M, "pedido.view_pedido")

urlpatterns = [
    path("cozinha", ver(views.CozinhaView.as_view()), name="cozinha"),
    path("cozinha/api/fila", ver(views.FilaJsonView.as_view()), name="cozinha-api-fila"),
    # Mover a fila: qualquer perfil com `mover_pedido` (Operador, Gestor e Administrador por padrão).
    path("cozinha/api/pedidos/<int:pk>/mover", module_required(M, "pedido.mover_pedido")(views.MoverJsonView.as_view()), name="cozinha-api-mover"),
    path("pedidos", ver(views.PedidosView.as_view()), name="pedidos"),
    path("pedidos/<int:pk>", ver(views.PedidoView.as_view()), name="pedido-detalhe"),
    path("pedidos/<int:pk>/acao", module_required(M, "pedido.mover_pedido")(views.PedidoAcaoView.as_view()), name="pedido-acao"),
    path("pedidos/enviar/<int:venda_id>", module_required(M, "pedido.add_pedido")(views.EnviarCozinhaView.as_view()), name="pedido-enviar"),
]
