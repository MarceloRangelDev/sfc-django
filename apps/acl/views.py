from django.contrib import messages
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from web_project import TemplateLayout
from web_project.acesso import perfis_visiveis

from .permissions_matrix import montar_matriz_permissoes
from .services import excluir_papel, salvar_papel


class PapelListView(ListView):
    """Lista de papéis (Group) cadastrados — "Papéis" no menu."""

    model = Group
    template_name = "acl_papel_list.html"
    context_object_name = "papeis"

    def get_queryset(self):
        # Contagens feitas no banco (uma consulta só), não por papel na tela.
        return perfis_visiveis(self.request.user).annotate(
            qtd_permissoes=Count("permissions", distinct=True), qtd_usuarios=Count("user", distinct=True)
        ).order_by("name")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class PapelFormView(View):
    """Tela única para criar OU editar um papel (Group), com a matriz de
    permissões módulo x ação — usada em /papeis/novo e /papeis/<id>/editar."""

    template_name = "acl_papel_form.html"

    def get(self, request, pk=None):
        grupo = get_object_or_404(perfis_visiveis(request.user), pk=pk) if pk else None
        marcadas = grupo.permissions.values_list("pk", flat=True) if grupo else []

        context = TemplateLayout.init(self, {
            "grupo": grupo,
            "matriz": montar_matriz_permissoes(marcadas),
        })
        return render(request, self.template_name, context)

    def post(self, request, pk=None):
        grupo = get_object_or_404(perfis_visiveis(request.user), pk=pk) if pk else None
        nome = (request.POST.get("nome") or "").strip()

        if not nome:
            messages.error(request, "Informe um nome para o papel.")
            context = TemplateLayout.init(self, {"grupo": grupo, "matriz": montar_matriz_permissoes()})
            return render(request, self.template_name, context)

        # Verifica duplicidade de nome (Group.name é único no Django).
        conflito = Group.objects.filter(name=nome).exclude(pk=grupo.pk if grupo else None)
        if conflito.exists():
            messages.error(request, f'Já existe um papel chamado "{nome}".')
            context = TemplateLayout.init(self, {"grupo": grupo, "matriz": montar_matriz_permissoes()})
            return render(request, self.template_name, context)

        # Validação dos ids de permissão, transação e auditoria: apps/acl/services.py
        try:
            grupo = salvar_papel(request, grupo, nome, request.POST.getlist("permissoes"))
        except ValidationError as erro:
            messages.error(request, erro.message)
            return redirect("acl-papel-list")

        messages.success(request, f'Papel "{grupo.name}" salvo com sucesso.')
        return redirect("acl-papel-list")


class PapelDeleteView(View):
    """Exclui um papel (Group). Usuários que tinham esse papel simplesmente
    deixam de ter as permissões associadas a ele — não são excluídos."""

    def post(self, request, pk):
        grupo = get_object_or_404(perfis_visiveis(request.user), pk=pk)
        nome = grupo.name
        try:
            excluir_papel(request, grupo)
        except ValidationError as erro:
            messages.error(request, erro.message)
            return redirect("acl-papel-list")
        messages.success(request, f'Papel "{nome}" excluído com sucesso.')
        return redirect("acl-papel-list")


class PermissaoListView(View):
    """Tela "Permissões": catálogo somente leitura das permissões nativas
    do Django usadas pelo sistema, organizadas por módulo."""

    template_name = "acl_permissao_list.html"

    def get(self, request):
        context = TemplateLayout.init(self, {"matriz": montar_matriz_permissoes()})
        return render(request, self.template_name, context)
