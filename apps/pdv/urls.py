from django.urls import path

from web_project.decorators import module_required, screen_required

from . import views
from .models import MovimentoCaixa

M = "pdv"
# Cada tela do menu do PDV é um módulo-filho (habilitação por cliente) com a sua permissão "Visualizar" (matriz de papéis).
TELAS = {
    "vender": ("pdv-vender", "pdv.view_telavender"), "comandas": ("pdv-comandas", "pdv.view_telacomandas"), "caixa": ("pdv-caixa", "pdv.view_telameucaixa"),
    "vendas": ("pdv-vendas", "pdv.view_televendas"), "caixas": ("pdv-caixas", "pdv.view_telacaixas"),
}


def p(permissao, view, tela=None):
    """Protege a rota com a permissão da AÇÃO e, quando é uma tela do menu, também com o módulo e a permissão da tela."""
    if tela is None:
        return module_required(M, f"pdv.{permissao}")(view.as_view())
    slug, permissao_tela = TELAS[tela]
    return screen_required(f"pdv.{permissao}")(module_required(slug, permissao_tela)(view.as_view()))


urlpatterns = [
    # Vender (balcão) e JSON da tela
    path("pdv", p("add_venda", views.VenderView, "vender"), name="pdv"),
    path("pdv/api/produtos", p("add_venda", views.ProdutosJsonView, "vender"), name="pdv-api-produtos"),
    path("pdv/api/finalizar", p("add_venda", views.FinalizarJsonView, "vender"), name="pdv-api-finalizar"),
    # Autorização por senha (excluir item lançado): usada pela tela de vender
    path("pdv/api/autorizar", p("add_venda", views.AutorizarJsonView), name="pdv-api-autorizar"),
    # Código de crachá (barras/QR) do próprio usuário para autorizar sem digitar a senha
    path("pdv/api/codigo-autorizacao", p("add_venda", views.CodigoAutorizacaoJsonView), name="pdv-api-codigo-autorizacao"),
    # Pergunta "Emitir nota?" ao finalizar: pede o documento fiscal da venda paga (idempotente)
    path("pdv/api/vendas/<int:pk>/nota", p("view_venda", views.NotaVendaJsonView), name="pdv-api-nota"),
    # Caixa
    path("pdv/caixa", p("view_caixa", views.CaixaView, "caixa"), name="pdv-caixa"),
    path("pdv/caixa/abrir", p("abrir_caixa", views.AbrirCaixaView, "caixa"), name="pdv-caixa-abrir"),
    path("pdv/caixa/fechar", p("abrir_caixa", views.FecharCaixaView, "caixa"), name="pdv-caixa-fechar"),
    path("pdv/caixa/sangria", screen_required("pdv.sangria")(module_required(*TELAS["caixa"])(views.MovimentoView.as_view(tipo=MovimentoCaixa.SANGRIA))), name="pdv-caixa-sangria"),
    path("pdv/caixa/suprimento", screen_required("pdv.suprimento")(module_required(*TELAS["caixa"])(views.MovimentoView.as_view(tipo=MovimentoCaixa.SUPRIMENTO))), name="pdv-caixa-suprimento"),
    path("pdv/caixas", p("view_caixa", views.CaixasView, "caixas"), name="pdv-caixas"),
    path("pdv/caixas/<int:pk>", p("view_caixa", views.CaixaDetalheView, "caixas"), name="pdv-caixa-detalhe"),
    path("pdv/caixas/<int:pk>/conferir", p("conferir_caixa", views.ConferirView, "caixas"), name="pdv-caixa-conferir"),
    # Comandas
    path("pdv/comandas", p("view_venda", views.ComandasView, "comandas"), name="pdv-comandas"),
    path("pdv/comandas/abrir", p("add_venda", views.AbrirComandaView, "comandas"), name="pdv-comanda-abrir"),
    path("pdv/comandas/<int:pk>", p("view_venda", views.ComandaView, "comandas"), name="pdv-comanda"),
    path("pdv/comandas/<int:pk>/itens", p("add_venda", views.ComandaItemView, "comandas"), name="pdv-comanda-itens"),
    path("pdv/comandas/<int:pk>/pagar", p("add_venda", views.ComandaPagarJsonView, "comandas"), name="pdv-comanda-pagar"),
    # Vendas. Cancelar e devolver (extornar) exigem a SENHA de quem tem a permissão (views.py / autorizacao.py).
    path("pdv/vendas", p("view_venda", views.VendasView, "vendas"), name="pdv-vendas"),
    path("pdv/vendas/<int:pk>", p("view_venda", views.VendaDetalheView, "vendas"), name="pdv-venda"),
    path("pdv/vendas/<int:pk>/cancelar", p("view_venda", views.CancelarVendaView), name="pdv-venda-cancelar"),
    path("pdv/vendas/<int:pk>/itens/<int:item>/devolver", p("view_venda", views.DevolverView), name="pdv-venda-devolver"),
    path("pdv/vendas/<int:pk>/comprovante", p("view_venda", views.ComprovanteView), name="pdv-comprovante"),
]
