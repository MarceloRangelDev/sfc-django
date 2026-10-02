from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao, registrar_exclusao
from web_project import TemplateLayout

from .permissions_matrix import ids_validos_da_matriz, montar_matriz_permissoes


def _alteracoes_de_permissoes(antes, depois):
    """Compara dois conjuntos de (codename, app_label) e devolve as
    alterações de auditoria: uma linha para as permissões concedidas e
    outra para as removidas."""
    def texto(conjunto):
        return ", ".join(sorted(f"{app}.{codigo}" for codigo, app in conjunto)) or None

    alteracoes = []
    if depois - antes:
        alteracoes.append({"campo": "permissoes_concedidas", "rotulo": "Permissões concedidas", "antes": None, "depois": texto(depois - antes)})
    if antes - depois:
        alteracoes.append({"campo": "permissoes_removidas", "rotulo": "Permissões removidas", "antes": texto(antes - depois), "depois": None})
    return alteracoes


class PapelListView(ListView):
    """Lista de papéis (Group) cadastrados — "Papéis" no menu."""

    model = Group
    template_name = "acl_papel_list.html"
    context_object_name = "papeis"

    def get_queryset(self):
        return Group.objects.all().order_by("name")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class PapelFormView(View):
    """Tela única para criar OU editar um papel (Group), com a matriz de
    permissões módulo x ação — usada em /papeis/novo e /papeis/<id>/editar."""

    template_name = "acl_papel_form.html"

    def get(self, request, pk=None):
        grupo = get_object_or_404(Group, pk=pk) if pk else None
        marcadas = grupo.permissions.values_list("pk", flat=True) if grupo else []

        context = TemplateLayout.init(self, {
            "grupo": grupo,
            "matriz": montar_matriz_permissoes(marcadas),
        })
        return render(request, self.template_name, context)

    def post(self, request, pk=None):
        grupo = get_object_or_404(Group, pk=pk) if pk else None
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

        criando = grupo is None
        # Só aceitamos ids de permissão que pertencem à matriz conhecida
        # (módulos de negócio + administrativos) — mesmo que alguém edite o
        # HTML do formulário e envie outro id (ex: de uma permissão de
        # gerenciar papéis), ele é ignorado aqui.
        ids_marcados = set(request.POST.getlist("permissoes"))
        ids_validos = ids_validos_da_matriz()
        ids_para_aplicar = {int(pid) for pid in ids_marcados if pid.isdigit() and int(pid) in ids_validos}

        with transaction.atomic():
            alteracoes = []
            if criando:
                grupo = Group.objects.create(name=nome)
                permissoes_antes = set()
            else:
                permissoes_antes = set(grupo.permissions.values_list("codename", "content_type__app_label"))
                if grupo.name != nome:
                    alteracoes.append({"campo": "name", "rotulo": "Nome", "antes": grupo.name, "depois": nome})
                    grupo.name = nome
                    grupo.save()

            grupo.permissions.set(Permission.objects.filter(pk__in=ids_para_aplicar))

            permissoes_depois = set(grupo.permissions.values_list("codename", "content_type__app_label"))
            alteracoes.extend(_alteracoes_de_permissoes(permissoes_antes, permissoes_depois))
            if criando:
                registrar_criacao(request, grupo, modulo="acl")
            elif alteracoes:
                registrar_alteracoes(request, grupo, alteracoes, modulo="acl")

        messages.success(request, f'Papel "{grupo.name}" salvo com sucesso.')
        return redirect("acl-papel-list")


class PapelDeleteView(View):
    """Exclui um papel (Group). Usuários que tinham esse papel simplesmente
    deixam de ter as permissões associadas a ele — não são excluídos."""

    def post(self, request, pk):
        grupo = get_object_or_404(Group, pk=pk)
        nome = grupo.name
        with transaction.atomic():
            registrar_exclusao(request, grupo, modulo="acl")  # antes do delete(): depois o objeto perde o id
            grupo.delete()
        messages.success(request, f'Papel "{nome}" excluído com sucesso.')
        return redirect("acl-papel-list")


class PermissaoListView(View):
    """Tela "Permissões": catálogo somente leitura das permissões nativas
    do Django usadas pelo sistema, organizadas por módulo."""

    template_name = "acl_permissao_list.html"

    def get(self, request):
        context = TemplateLayout.init(self, {"matriz": montar_matriz_permissoes()})
        return render(request, self.template_name, context)
