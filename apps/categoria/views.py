from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from web_project import TemplateLayout
from web_project.mixins import ClienteScopedMixin

from .forms import CategoriaForm
from .models import Categoria


class CategoriaFormKwargsMixin:
    """Passa o Cliente do usuário logado para o CategoriaForm, para que ele
    valide duplicidade de nome só dentro do próprio cliente."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["cliente"] = self.request.user.profile.cliente
        return kwargs


class CategoriaListView(ClienteScopedMixin, ListView):
    model = Categoria
    template_name = "categoria.html"
    context_object_name = "categorias"
    paginate_by = 25

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class CategoriaCreateView(CategoriaFormKwargsMixin, ClienteScopedMixin, CreateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = "categoria_form.html"
    success_url = reverse_lazy("categoria")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Categoria cadastrada com sucesso.")
        return super().form_valid(form)


class CategoriaUpdateView(CategoriaFormKwargsMixin, ClienteScopedMixin, UpdateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = "categoria_form.html"
    success_url = reverse_lazy("categoria")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        messages.success(self.request, "Categoria atualizada com sucesso.")
        return super().form_valid(form)
