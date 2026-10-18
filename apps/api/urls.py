from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

from web_project.acesso import administrador_global

from . import assinatura as assinatura_api, kanban as kanban_api, cadastros, compras, consumidores, cotacoes, fiscal, imagens, integracoes, maodeobra, pdv, pedidos, estoque_financeiro, views, views_operacao

router = SimpleRouter()
router.register("insumos", views.InsumoViewSet, basename="api-insumo")
router.register("medidas", views.MedidaViewSet, basename="api-medida")
router.register("categorias", views.CategoriaViewSet, basename="api-categoria")
router.register("usuarios", views.UsuarioViewSet, basename="api-usuario")
router.register("clientes", views.ClienteViewSet, basename="api-cliente")
router.register("modulos", views.ModuloViewSet, basename="api-modulo")
router.register("perfis", views.PerfilViewSet, basename="api-perfil")
router.register("permissoes", views.PermissaoViewSet, basename="api-permissao")
router.register("auditoria", views.AuditoriaViewSet, basename="api-auditoria")
router.register("custos-comuns", views_operacao.CustoComumViewSet, basename="api-custo-comum")
router.register("estoque/saldos", estoque_financeiro.EstoqueSaldoViewSet, basename="api-estoque-saldo")
router.register("estoque/alertas", estoque_financeiro.EstoqueAlertasViewSet, basename="api-estoque-alertas")
router.register("estoque/transferencias", estoque_financeiro.EstoqueTransferenciaViewSet, basename="api-estoque-transferencia")
router.register("financeiro/categorias", estoque_financeiro.CategoriaFinanceiraViewSet, basename="api-financeiro-categoria")
router.register("financeiro/contas-pagar", estoque_financeiro.ContaPagarViewSet, basename="api-conta-pagar")
router.register("financeiro/contas-receber", estoque_financeiro.ContaReceberViewSet, basename="api-conta-receber")
router.register("financeiro/dre", estoque_financeiro.DreViewSet, basename="api-financeiro-dre")
router.register("financeiro/balanco", estoque_financeiro.BalancoViewSet, basename="api-financeiro-balanco")
router.register("painel", estoque_financeiro.PainelViewSet, basename="api-painel")
router.register("consumidores", consumidores.ConsumidorViewSet, basename="api-consumidor")
router.register("formas-pagamento", pdv.FormaPagamentoViewSet, basename="api-forma-pagamento")
router.register("mesas", pdv.MesaViewSet, basename="api-mesa")
router.register("pdv/produtos", pdv.ProdutoPdvViewSet, basename="api-pdv-produto")
router.register("pdv/caixa", pdv.CaixaViewSet, basename="api-pdv-caixa")
router.register("pdv/vendas", pdv.VendaViewSet, basename="api-pdv-venda")
router.register("pdv/comandas", pdv.ComandaViewSet, basename="api-pdv-comanda")
router.register("pedidos", pedidos.PedidoViewSet, basename="api-pedido")
router.register("integracoes", integracoes.ConexaoViewSet, basename="api-integracao")
router.register("mensagens", integracoes.MensagemViewSet, basename="api-mensagem")
router.register("compromissos", integracoes.CompromissoViewSet, basename="api-compromisso")
router.register("fiscal/documentos", fiscal.DocumentoFiscalViewSet, basename="api-documento-fiscal")
router.register("colaboradores", maodeobra.ColaboradorViewSet, basename="api-colaborador")
router.register("apontamentos", maodeobra.ApontamentoViewSet, basename="api-apontamento")
router.register("cotacoes", cotacoes.CotacaoViewSet, basename="api-cotacao")
router.register("compras/itens", compras.ItemCompraViewSet, basename="api-compra-item")
router.register("compras/sugestoes", compras.SugestaoCompraViewSet, basename="api-compra-sugestao")
router.register("produtos", cadastros.ProdutoViewSet, basename="api-produto")
router.register("canais-venda", cadastros.CanalViewSet, basename="api-canal")
router.register("custos-operacionais", cadastros.CustoViewSet, basename="api-custo")
router.register("processados", cadastros.ProcessadoViewSet, basename="api-processado")
router.register("fichas-tecnicas", cadastros.FichaViewSet, basename="api-ficha")
router.register("precificacoes", cadastros.PrecificacaoViewSet, basename="api-precificacao")
router.register("estoque/movimentos", views_operacao.MovimentoEstoqueViewSet, basename="api-estoque-movimento")
router.register("custo-operacional/categorias", views_operacao.CustoCategoriaViewSet, basename="api-custo-categoria")


def somente_administrador(view):
    """Documentação (schema/Swagger/Redoc): exige login na sessão web E ser
    administrador — é a ferramenta do administrador para testar a API. O
    schema descreve toda a superfície da API, então não fica público."""

    @login_required
    def protegida(request, *args, **kwargs):
        if not administrador_global(request.user):
            raise PermissionDenied("Somente administradores podem acessar a documentação da API.")
        return view(request, *args, **kwargs)

    return protegida


urlpatterns = [
    # Autenticação
    path("api/v1/pdv/configuracao/", pdv.ConfiguracaoPdvView.as_view(), name="api-pdv-config"),
    path("api/v1/fiscal/configuracao/", fiscal.FiscalConfiguracaoViewSet.as_view({"get": "list"}), name="api-fiscal-config"),
    path("api/v1/assinatura/", assinatura_api.AssinaturaView.as_view(), name="api-assinatura"),
    path("api/v1/assinatura/assinar/", assinatura_api.AssinarView.as_view(), name="api-assinatura-assinar"),
    path("api/v1/assinatura/cancelar/", assinatura_api.CancelarView.as_view(), name="api-assinatura-cancelar"),
    path("api/v1/kanban/quadro/", kanban_api.QuadroView.as_view(), name="api-kanban-quadro"),
    path("api/v1/kanban/colunas/", kanban_api.ColunasView.as_view(), name="api-kanban-colunas"),
    path("api/v1/kanban/colunas/<int:pk>/", kanban_api.ColunaView.as_view(), name="api-kanban-coluna"),
    path("api/v1/kanban/cartoes/", kanban_api.CartoesView.as_view(), name="api-kanban-cartoes"),
    path("api/v1/kanban/cartoes/<int:pk>/", kanban_api.CartaoView.as_view(), name="api-kanban-cartao"),
    path("api/v1/kanban/cartoes/<int:pk>/mover/", kanban_api.CartaoMoverView.as_view(), name="api-kanban-cartao-mover"),
    path("api/v1/mao-de-obra/regras/", maodeobra.ConfiguracaoMaoDeObraView.as_view(), name="api-mao-obra-config"),
    path("api/v1/mao-de-obra/relatorio/", maodeobra.RelatorioMaoDeObraView.as_view(), name="api-mao-obra-relatorio"),
    path("api/v1/imagens/<str:tipo>/<int:pk>/", imagens.ImagemEntidadeView.as_view(), name="api-imagem"),
    path("api/v1/plataforma/imagens/configuracao/", imagens.ConfiguracaoImagensView.as_view(), name="api-imagens-config"),
    path("api/v1/plataforma/imagens/custos/", imagens.CustosImagensView.as_view(), name="api-imagens-custos"),
    path("api/v1/plataforma/imagens/tarifas/", imagens.TarifasImagensView.as_view(), name="api-imagens-tarifas"),
    path("api/v1/auth/login/", views.LoginView.as_view(), name="api-login"),
    path("api/v1/auth/logout/", views.LogoutView.as_view(), name="api-logout"),
    path("api/v1/auth/me/", views.MeView.as_view(), name="api-me"),
    path("api/v1/auth/register/", views.CadastroView.as_view(), name="api-register"),
    path("api/v1/auth/password/change/", views.AlterarSenhaView.as_view(), name="api-password-change"),
    path("api/v1/auth/password/forgot/", views.EsqueciSenhaView.as_view(), name="api-password-forgot"),
    path("api/v1/auth/password/reset/", views.RedefinirSenhaView.as_view(), name="api-password-reset"),
    # Recursos
    path("api/v1/", include(router.urls)),
    # Documentação OpenAPI (gerada do código)
    path("api/schema/", somente_administrador(SpectacularAPIView.as_view()), name="api-schema"),
    path("api/docs/", somente_administrador(SpectacularSwaggerView.as_view(url_name="api-schema")), name="api-docs"),
    path("api/redoc/", somente_administrador(SpectacularRedocView.as_view(url_name="api-schema")), name="api-redoc"),
]
