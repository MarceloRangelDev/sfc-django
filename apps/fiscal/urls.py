from django.urls import path

from web_project.decorators import module_required

from . import views

F, C = "fiscal", "fiscal-config"

urlpatterns = [
    path("fiscal", module_required(F, "fiscal.view_documentofiscal")(views.DocumentoListView.as_view()), name="fiscal-documentos"),
    path("fiscal/processar", module_required(F, "fiscal.emitir_nota")(views.ProcessarView.as_view()), name="fiscal-processar"),
    path("fiscal/documentos/<int:pk>", module_required(F, "fiscal.view_documentofiscal")(views.DocumentoView.as_view()), name="fiscal-documento"),
    path("fiscal/documentos/<int:pk>/cancelar", module_required(F, "fiscal.emitir_nota")(views.CancelarView.as_view()), name="fiscal-cancelar"),
    path("fiscal/solicitar/<int:venda_id>", module_required(F, "fiscal.emitir_nota")(views.SolicitarView.as_view()), name="fiscal-solicitar"),
    path("fiscal/configuracao", module_required(C, "fiscal.change_configuracaofiscal")(views.ConfiguracaoView.as_view()), name="fiscal-config"),
]
