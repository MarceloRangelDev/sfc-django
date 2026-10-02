from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView

from apps.auditoria.utils import registrar_alteracoes
from web_project import TemplateLayout

from apps.cliente.models import Cliente

from .models import ClienteModulo, Modulo


class ModuloClienteView(TemplateView):
    """Tela "Módulos do Cliente": permite a um Administrador escolher, por
    Cliente, quais módulos (Insumos, Categorias, Receitas...) ficam
    disponíveis — ex: um cliente do segmento de alimentação recebe o
    módulo de Fichas Técnicas, outro segmento pode não precisar dele."""

    template_name = "modulo_cliente.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))

        clientes = Cliente.objects.all().order_by("nome_razao_social")
        cliente_id = self.request.GET.get("cliente")
        cliente_selecionado = None
        if cliente_id:
            cliente_selecionado = Cliente.objects.filter(pk=cliente_id).first()
        elif clientes.exists():
            cliente_selecionado = clientes.first()

        modulos = Modulo.objects.all().order_by("nome")
        habilitados = set()
        if cliente_selecionado:
            habilitados = set(
                ClienteModulo.objects.filter(cliente=cliente_selecionado, ativo=True).values_list(
                    "modulo_id", flat=True
                )
            )

        context["clientes"] = clientes
        context["cliente_selecionado"] = cliente_selecionado
        context["modulos"] = modulos
        context["modulos_habilitados_ids"] = habilitados
        return context

    def post(self, request, *args, **kwargs):
        cliente = get_object_or_404(Cliente, pk=request.POST.get("cliente_id"))
        # Os módulos marcados chegam como uma lista de ids no POST; qualquer
        # módulo do sistema que não estiver nessa lista é desabilitado para
        # o cliente — nunca confiamos em nada além do próprio "cliente_id"
        # do formulário para decidir QUAL cliente está sendo alterado (o
        # acesso à tela já garante que quem chegou aqui tem permissão de
        # administração de módulos).
        modulos_marcados = set(request.POST.getlist("modulos"))

        with transaction.atomic():
            estado_anterior = dict(
                ClienteModulo.objects.filter(cliente=cliente).values_list("modulo_id", "ativo")
            )
            alteracoes = []
            for modulo in Modulo.objects.all():
                habilitado = str(modulo.pk) in modulos_marcados
                ClienteModulo.objects.update_or_create(
                    cliente=cliente, modulo=modulo, defaults={"ativo": habilitado}
                )
                if estado_anterior.get(modulo.pk, False) != habilitado:
                    alteracoes.append({
                        "campo": f"modulo_{modulo.slug}", "rotulo": f"Módulo {modulo.nome}",
                        "antes": "Habilitado" if estado_anterior.get(modulo.pk, False) else "Desabilitado",
                        "depois": "Habilitado" if habilitado else "Desabilitado",
                    })
            if alteracoes:
                registrar_alteracoes(request, cliente, alteracoes, modulo="modulo")

        messages.success(request, f"Módulos atualizados para {cliente.nome_razao_social}.")
        return redirect(f"{request.path}?cliente={cliente.pk}")
