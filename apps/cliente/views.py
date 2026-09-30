from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from web_project import TemplateLayout

from .forms import ClienteForm
from .models import Cliente


class ClienteListView(ListView):
    """Lista de Clientes (tenants) cadastrados no sistema."""

    model = Cliente
    template_name = "cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 25

    def get_queryset(self):
        return Cliente.objects.all().order_by("nome_razao_social")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class ClienteCreateView(CreateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "cliente_form.html"
    success_url = reverse_lazy("cliente-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Cliente cadastrado com sucesso.")
        return super().form_valid(form)


class ClienteUpdateView(UpdateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "cliente_form.html"
    success_url = reverse_lazy("cliente-list")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["usuarios_vinculados"] = self.object.usuarios.select_related("user").order_by("user__username")
        # Só usuários que ainda não têm Cliente aparecem para vincular —
        # já que, por ora, cada usuário pertence a exatamente um Cliente.
        context["usuarios_disponiveis"] = User.objects.filter(profile__cliente__isnull=True).order_by("username")
        return context

    def form_valid(self, form):
        messages.success(self.request, "Cliente atualizado com sucesso.")
        return super().form_valid(form)


class ClienteVincularUsuarioView(View):
    """Vincula um usuário existente (ainda sem Cliente) ao Cliente da URL."""

    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        # O usuário só é resolvido entre os que ainda não têm cliente, e o
        # Cliente de destino vem sempre do "pk" da própria URL (checado por
        # screen_required/permissão antes de chegar aqui) — nunca de um
        # campo "cliente_id" solto no POST, para não permitir que alguém
        # manipule a requisição e vincule um usuário a um Cliente qualquer.
        user_id = request.POST.get("user_id")
        user = get_object_or_404(User, pk=user_id, profile__cliente__isnull=True)
        user.profile.cliente = cliente
        user.profile.save()
        messages.success(request, f"Usuário \"{user.username}\" vinculado a {cliente.nome_razao_social}.")
        return redirect("cliente-edit", pk=cliente.pk)


class ClienteDesvincularUsuarioView(View):
    """Remove o vínculo de um usuário com o Cliente (não apaga o usuário)."""

    def post(self, request, pk, user_id):
        cliente = get_object_or_404(Cliente, pk=pk)
        # Só desvincula se o usuário realmente pertencer a ESTE cliente —
        # evita que, trocando o user_id na URL, alguém desvincule um
        # usuário de um Cliente diferente do informado no "pk".
        user = get_object_or_404(User, pk=user_id, profile__cliente=cliente)
        user.profile.cliente = None
        user.profile.save()
        messages.success(request, f"Usuário \"{user.username}\" desvinculado de {cliente.nome_razao_social}.")
        return redirect("cliente-edit", pk=cliente.pk)
