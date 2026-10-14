import re

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin

from .forms import ConsumidorForm
from .models import Consumidor
from .services import (
    ConsultaIndisponivel, ConsultaInvalida, ConsultaLimite, ConsultaNaoEncontrada, anonimizar_consumidor,
    consultar_cep, consultar_cnpj,
)


class ConsumidorListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    """Lista paginada no escopo de unidades do usuário. Mostra o documento MASCARADO e nenhum contato:
    o dado completo só aparece no formulário de edição (permissão `change`)."""

    model = Consumidor
    template_name = "consumidor.html"
    context_object_name = "consumidores"
    colunas_ordenaveis = {"nome": "nome", "cidade": "cidade", "criado_em": "criado_em"}

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            filtro = Q(nome__icontains=termo) | Q(cidade__icontains=termo)
            digitos = re.sub(r"\D", "", termo)
            if len(digitos) >= 3:  # pesquisa por trecho do documento, sem exibi-lo
                filtro |= Q(documento__contains=digitos)
            queryset = queryset.filter(filtro)
        return self.ordenar(self.filtrar_status(queryset))

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ConsumidorFormMixin:
    model = Consumidor
    form_class = ConsumidorForm
    template_name = "consumidor_form.html"
    success_url = reverse_lazy("consumidor")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ConsumidorCreateView(ConsumidorFormMixin, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    acao_escopo = "add"
    nome_url_novo = "consumidor-create"

    def form_valid(self, form):
        messages.success(self.request, "Consumidor cadastrado com sucesso.")
        return super().form_valid(form)


class ConsumidorUpdateView(ConsumidorFormMixin, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    acao_escopo = "change"

    def get_queryset(self):
        return super().get_queryset().filter(anonimizado_em__isnull=True)  # anonimizado não se edita

    def form_valid(self, form):
        messages.success(self.request, "Consumidor atualizado com sucesso.")
        return super().form_valid(form)


class ConsumidorAnonimizarView(View):
    """POST: anonimiza (LGPD) um consumidor do escopo de exclusão do usuário. Irreversível."""

    def post(self, request, pk):
        consumidor = get_object_or_404(Consumidor, pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"))
        anonimizar_consumidor(request, consumidor)
        messages.success(request, "Dados pessoais apagados: o consumidor foi anonimizado.")
        return redirect("consumidor")


class _ConsultaView(View):
    """GET JSON de uma consulta externa (CEP/CNPJ). Só LEITURA e sugestão: nada é gravado."""

    funcao = None
    parametro = None

    def get(self, request):
        try:
            return JsonResponse(self.funcao(request.user, request.GET.get(self.parametro, "")))
        except ConsultaInvalida as erro:
            return JsonResponse({"erro": str(erro)}, status=400)
        except ConsultaNaoEncontrada as erro:
            return JsonResponse({"erro": str(erro)}, status=404)
        except ConsultaLimite as erro:
            resposta = JsonResponse({"erro": str(erro)}, status=429)
            resposta["Retry-After"] = str(erro.espera)
            return resposta
        except ConsultaIndisponivel as erro:
            return JsonResponse({"erro": str(erro)}, status=503)


class ConsultaCepView(_ConsultaView):
    funcao = staticmethod(consultar_cep)
    parametro = "cep"


class ConsultaCnpjView(_ConsultaView):
    funcao = staticmethod(consultar_cnpj)
    parametro = "cnpj"
