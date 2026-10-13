from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView

from apps.acl.services import efeitos_do_usuario, matriz_do_usuario
from web_project import TemplateLayout
from web_project.acesso import clientes_acessiveis, perfis_visiveis, usuarios_acessiveis

from .forms import UsuarioForm
from .services import alternar_ativo, foto_usuario, salvar_usuario

OPCOES_POR_PAGINA = [10, 25, 50, 100]


class UsuarioListView(ListView):
    template_name = "usuario_list.html"
    context_object_name = "usuarios"

    def get_queryset(self):
        queryset = usuarios_acessiveis(self.request.user)
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            queryset = queryset.filter(
                Q(username__icontains=termo) | Q(email__icontains=termo)
                | Q(first_name__icontains=termo) | Q(last_name__icontains=termo)
            )
        if params.get("cliente", "").isdigit():
            queryset = queryset.filter(profile__cliente_id=params["cliente"])
        if params.get("perfil", "").isdigit():
            queryset = queryset.filter(groups__pk=params["perfil"])
        if params.get("status") == "ativo":
            queryset = queryset.filter(is_active=True)
        elif params.get("status") == "inativo":
            queryset = queryset.filter(is_active=False)
        return queryset.order_by("username")

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", 25))
        except (TypeError, ValueError):
            valor = 25
        return valor if valor in OPCOES_POR_PAGINA else 25

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        solicitante = self.request.user
        context.update(
            filtros=self.request.GET,
            clientes=clientes_acessiveis(solicitante).order_by("nome_razao_social"),
            perfis=perfis_visiveis(solicitante).order_by("name"),
            per_page=self.get_paginate_by(None),
            opcoes_por_pagina=OPCOES_POR_PAGINA,
        )
        return context


class UsuarioFormView(FormView):
    """Tela única de cadastro (/usuarios/novo) e edição (/usuarios/<id>/editar)."""

    template_name = "usuario_form.html"
    form_class = UsuarioForm
    success_url = reverse_lazy("usuario-list")

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        self.usuario = get_object_or_404(usuarios_acessiveis(request.user), pk=pk) if pk else None
        # Foto "antes" para a auditoria (o formulário altera o objeto ao validar).
        self.foto_antes = foto_usuario(self.usuario) if self.usuario else None
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(solicitante=self.request.user, instance=self.usuario)
        return kwargs

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["usuario"] = self.usuario
        # Mesma matriz da edição de papéis; num POST inválido mantém o que foi marcado.
        efeitos = efeitos_do_usuario(self.usuario)
        if context["form"].is_bound:
            efeitos.update(context["form"].excecoes or {})
        context["matriz"] = matriz_do_usuario(efeitos)
        return context

    def form_valid(self, form):
        salvar_usuario(self.request, form, self.foto_antes)
        messages.success(self.request, "Usuário atualizado com sucesso." if self.usuario else "Usuário cadastrado com sucesso.")
        return super().form_valid(form)


class UsuarioAlternarAtivoView(View):
    def post(self, request, pk):
        usuario = get_object_or_404(usuarios_acessiveis(request.user), pk=pk)
        if usuario.pk == request.user.pk:
            messages.error(request, "Você não pode inativar a sua própria conta.")
            return redirect("usuario-list")
        alternar_ativo(request, usuario)
        messages.success(request, f'Usuário "{usuario.username}" {"ativado" if usuario.is_active else "inativado"}.')
        return redirect("usuario-list")
