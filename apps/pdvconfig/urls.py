from django.urls import path

from web_project.decorators import module_required

from . import views

# Módulos de Configurações (essenciais: só dependem da permissão do perfil).
FP, ME, CF = "forma-pagamento", "mesa", "pdv-config"

urlpatterns = [
    path("formas-pagamento", module_required(FP, "pdvconfig.view_formapagamento")(views.FormaPagamentoListView.as_view()), name="forma-pagamento-list"),
    path("formas-pagamento/nova", module_required(FP, "pdvconfig.add_formapagamento")(views.FormaPagamentoCreateView.as_view()), name="forma-pagamento-create"),
    path("formas-pagamento/padrao", module_required(FP, "pdvconfig.add_formapagamento")(views.FormasPadraoView.as_view()), name="forma-pagamento-padrao"),
    path("formas-pagamento/<int:pk>/editar", module_required(FP, "pdvconfig.change_formapagamento")(views.FormaPagamentoUpdateView.as_view()), name="forma-pagamento-edit"),
    path("mesas", module_required(ME, "pdvconfig.view_mesa")(views.MesaListView.as_view()), name="mesa-list"),
    path("mesas/nova", module_required(ME, "pdvconfig.add_mesa")(views.MesaCreateView.as_view()), name="mesa-create"),
    path("mesas/<int:pk>/editar", module_required(ME, "pdvconfig.change_mesa")(views.MesaUpdateView.as_view()), name="mesa-edit"),
    path("pdv-config", module_required(CF, "pdvconfig.change_configuracaopdv")(views.ConfiguracaoPdvView.as_view()), name="pdv-config"),
]
