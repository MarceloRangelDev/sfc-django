from django.urls import path

from web_project.decorators import module_required

from .views import (
    ConsultaCepView, ConsultaCnpjView, ConsumidorAnonimizarView, ConsumidorCreateView, ConsumidorListView,
    ConsumidorUpdateView,
)

M = "consumidor"

urlpatterns = [
    path("consumidor", module_required(M, "consumidor.view_consumidor")(ConsumidorListView.as_view()), name="consumidor"),
    path("consumidor/novo", module_required(M, "consumidor.add_consumidor")(ConsumidorCreateView.as_view()), name="consumidor-create"),
    # Consultas externas (sugestão para pré-preencher o formulário): exigem poder criar/editar.
    path("consumidor/consulta-cep", module_required(M, "consumidor.add_consumidor")(ConsultaCepView.as_view()), name="consumidor-consulta-cep"),
    path("consumidor/consulta-cnpj", module_required(M, "consumidor.add_consumidor")(ConsultaCnpjView.as_view()), name="consumidor-consulta-cnpj"),
    path("consumidor/<int:pk>/editar", module_required(M, "consumidor.change_consumidor")(ConsumidorUpdateView.as_view()), name="consumidor-edit"),
    path("consumidor/<int:pk>/anonimizar", module_required(M, "consumidor.delete_consumidor")(ConsumidorAnonimizarView.as_view()), name="consumidor-anonimizar"),
]
