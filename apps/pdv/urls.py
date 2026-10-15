from django.urls import path

from web_project.decorators import module_required

from . import views
from .models import MovimentoCaixa

M = "pdv"


def p(permissao, view):
    return module_required(M, f"pdv.{permissao}")(view.as_view())


urlpatterns = [
    # Vender (balcão) e JSON da tela
    path("pdv", p("add_venda", views.VenderView), name="pdv"),
    path("pdv/api/produtos", p("add_venda", views.ProdutosJsonView), name="pdv-api-produtos"),
    path("pdv/api/finalizar", p("add_venda", views.FinalizarJsonView), name="pdv-api-finalizar"),
    # Caixa
    path("pdv/caixa", p("view_caixa", views.CaixaView), name="pdv-caixa"),
    path("pdv/caixa/abrir", p("abrir_caixa", views.AbrirCaixaView), name="pdv-caixa-abrir"),
    path("pdv/caixa/fechar", p("abrir_caixa", views.FecharCaixaView), name="pdv-caixa-fechar"),
    path("pdv/caixa/sangria", module_required(M, "pdv.sangria")(views.MovimentoView.as_view(tipo=MovimentoCaixa.SANGRIA)), name="pdv-caixa-sangria"),
    path("pdv/caixa/suprimento", module_required(M, "pdv.suprimento")(views.MovimentoView.as_view(tipo=MovimentoCaixa.SUPRIMENTO)), name="pdv-caixa-suprimento"),
    path("pdv/caixas", p("view_caixa", views.CaixasView), name="pdv-caixas"),
    path("pdv/caixas/<int:pk>", p("view_caixa", views.CaixaDetalheView), name="pdv-caixa-detalhe"),
    path("pdv/caixas/<int:pk>/conferir", p("conferir_caixa", views.ConferirView), name="pdv-caixa-conferir"),
    # Comandas
    path("pdv/comandas", p("view_venda", views.ComandasView), name="pdv-comandas"),
    path("pdv/comandas/abrir", p("add_venda", views.AbrirComandaView), name="pdv-comanda-abrir"),
    path("pdv/comandas/<int:pk>", p("view_venda", views.ComandaView), name="pdv-comanda"),
    path("pdv/comandas/<int:pk>/itens", p("add_venda", views.ComandaItemView), name="pdv-comanda-itens"),
    path("pdv/comandas/<int:pk>/pagar", p("add_venda", views.ComandaPagarJsonView), name="pdv-comanda-pagar"),
    # Vendas
    path("pdv/vendas", p("view_venda", views.VendasView), name="pdv-vendas"),
    path("pdv/vendas/<int:pk>", p("view_venda", views.VendaDetalheView), name="pdv-venda"),
    path("pdv/vendas/<int:pk>/cancelar", p("cancelar_venda", views.CancelarVendaView), name="pdv-venda-cancelar"),
    path("pdv/vendas/<int:pk>/itens/<int:item>/devolver", p("devolver_item", views.DevolverView), name="pdv-venda-devolver"),
    path("pdv/vendas/<int:pk>/comprovante", p("view_venda", views.ComprovanteView), name="pdv-comprovante"),
]
