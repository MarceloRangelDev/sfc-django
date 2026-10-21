import re

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import registrar_alteracoes
from apps.modulo.models import Segmento
from web_project import TemplateLayout
from web_project.acesso import (
    administrador_global,
    clientes_acessiveis,
    usuarios_acessiveis,
    validar_vinculo_admin,
)

from .forms import ClienteForm
from .models import Cliente
from .services import criar_cliente
from .validators import normalizar_documento

PADRAO_POR_PAGINA = 25
OPCOES_POR_PAGINA = [10, 25, 50, 100]


class ClienteFormKwargsMixin:
    """Escopo: o queryset é sempre o da estrutura matriz/filiais do usuário
    (clientes_acessiveis), então trocar o pk na URL resulta em 404."""

    def get_queryset(self):
        return clientes_acessiveis(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["solicitante"] = self.request.user
        return kwargs


class ClienteListView(ListView):
    """Lista de Clientes da estrutura (matriz/filiais) do usuário; o Admin vê todos.

    Filtros e paginação são aplicados no banco, sempre sobre
    clientes_acessiveis(): nenhum filtro alcança clientes de fora do escopo."""

    model = Cliente
    template_name = "cliente_list.html"
    context_object_name = "clientes"

    def get_queryset(self):
        queryset = (
            clientes_acessiveis(self.request.user)
            .select_related("matriz", "segmento")
            .annotate(qtd_usuarios=Count("usuarios"))
        )
        params = self.request.GET
        termo = params.get("q", "").strip()
        if termo:
            filtro = Q(nome_razao_social__icontains=termo) | Q(nome_fantasia__icontains=termo)
            if re.fullmatch(r"[\d.\-/\s]+", termo):  # CPF/CNPJ digitado com ou sem máscara
                filtro |= Q(cpf_cnpj__contains=normalizar_documento(termo))
            queryset = queryset.filter(filtro)
        if params.get("tipo_pessoa") in dict(Cliente.TIPO_PESSOA_CHOICES):
            queryset = queryset.filter(tipo_pessoa=params["tipo_pessoa"])
        if params.get("tipo_estabelecimento") in dict(Cliente.TIPO_ESTABELECIMENTO_CHOICES):
            queryset = queryset.filter(tipo_estabelecimento=params["tipo_estabelecimento"])
        if params.get("segmento", "").isdigit():
            queryset = queryset.filter(segmento_id=params["segmento"])
        if params.get("status") == "ativo":
            queryset = queryset.filter(ativo=True)
        elif params.get("status") == "inativo":
            queryset = queryset.filter(ativo=False)
        return queryset.order_by("nome_razao_social", "pk")

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", PADRAO_POR_PAGINA))
        except (TypeError, ValueError):
            valor = PADRAO_POR_PAGINA
        return valor if valor in OPCOES_POR_PAGINA else PADRAO_POR_PAGINA

    def paginate_queryset(self, queryset, page_size):
        # Página fora do intervalo (ex.: o filtro reduziu o total de páginas)
        # cai na última página em vez de dar 404.
        paginator = self.get_paginator(queryset, page_size)
        try:
            numero = int(self.request.GET.get("page", 1))
        except (TypeError, ValueError):
            numero = 1
        pagina = paginator.get_page(numero)
        return paginator, pagina, pagina.object_list, pagina.paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            filtros=self.request.GET,
            segmentos=Segmento.objects.filter(clientes__in=clientes_acessiveis(self.request.user)).distinct().order_by("nome"),
            tipos_pessoa=Cliente.TIPO_PESSOA_CHOICES,
            tipos_estabelecimento=Cliente.TIPO_ESTABELECIMENTO_CHOICES,
            per_page=self.get_paginate_by(None),
            opcoes_por_pagina=OPCOES_POR_PAGINA,
        )
        return context


class ClienteCreateView(ClienteFormKwargsMixin, AuditoriaFormMixin, CreateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "cliente_form.html"
    success_url = reverse_lazy("cliente-list")

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        # Cliente + usuário Gestor + auditoria numa transação (apps/cliente/services.py).
        try:
            self.object = criar_cliente(
                self.request, form.save(commit=False), form.cleaned_data["perfis"], form.cleaned_data["email_gestor"],
                form.cleaned_data["usuario_nome"], form.cleaned_data["usuario_username"],
            )
        except ValidationError as erro:  # nada foi gravado (transação desfeita)
            form.add_error(None, erro)
            return self.form_invalid(form)
        messages.success(self.request, "Cliente cadastrado com sucesso. O usuário inicial foi criado e recebe por e-mail o link para definir a senha.")
        return redirect(self.get_success_url())


class ClienteUpdateView(ClienteFormKwargsMixin, AuditoriaFormMixin, UpdateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "cliente_form.html"
    success_url = reverse_lazy("cliente-list")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["usuarios_vinculados"] = self.object.usuarios.select_related("user").order_by("user__username")
        # Vincular usuário sem cliente é exclusivo do Admin (a lista de
        # usuários sem cliente é global).
        context["pode_vincular"] = administrador_global(self.request.user)
        # Aba Pagamentos/Assinatura: só com permissão e dentro da estrutura que o usuário alcança.
        try:
            from apps.assinatura.services import painel_do_cliente

            context["assinatura_painel"] = painel_do_cliente(self.request.user, self.object.pk)
        except (PermissionDenied, Http404):
            context["assinatura_painel"] = None
        if context["pode_vincular"]:
            context["usuarios_disponiveis"] = User.objects.filter(profile__cliente__isnull=True).order_by("username")
        return context

    def form_valid(self, form):
        messages.success(self.request, "Cliente atualizado com sucesso.")
        return super().form_valid(form)


class ClienteVincularUsuarioView(View):
    """Vincula um usuário existente (ainda sem Cliente) ao Cliente da URL."""

    def post(self, request, pk):
        if not administrador_global(request.user):
            return redirect("pages-misc-not-authorized")
        cliente = get_object_or_404(clientes_acessiveis(request.user), pk=pk)
        # O usuário só é resolvido entre os que ainda não têm cliente, e o
        # Cliente de destino vem sempre do "pk" da própria URL — nunca de um
        # campo "cliente_id" solto no POST.
        user = get_object_or_404(User, pk=request.POST.get("user_id"), profile__cliente__isnull=True)
        # O perfil Admin só pode ficar no cliente do sistema.
        for perfil in user.groups.all():
            erro = validar_vinculo_admin(perfil, cliente)
            if erro:
                messages.error(request, erro)
                return redirect("cliente-edit", pk=cliente.pk)
        with transaction.atomic():
            user.profile.cliente = cliente
            user.profile.save()
            registrar_alteracoes(
                request, cliente,
                [{"campo": "usuarios", "rotulo": "Usuário vinculado", "antes": None, "depois": user.username}],
            )
        messages.success(request, f"Usuário \"{user.username}\" vinculado a {cliente.nome_razao_social}.")
        return redirect("cliente-edit", pk=cliente.pk)


class ClienteDesvincularUsuarioView(View):
    """Remove o vínculo de um usuário com o Cliente (não apaga o usuário)."""

    def post(self, request, pk, user_id):
        cliente = get_object_or_404(clientes_acessiveis(request.user), pk=pk)
        # Só desvincula se o usuário realmente pertencer a ESTE cliente e for
        # gerenciável por quem pede (ex.: um Admin só sai pelo Admin).
        user = get_object_or_404(usuarios_acessiveis(request.user), pk=user_id, profile__cliente=cliente)
        with transaction.atomic():
            user.profile.cliente = None
            user.profile.save()
            registrar_alteracoes(
                request, cliente,
                [{"campo": "usuarios", "rotulo": "Usuário vinculado", "antes": user.username, "depois": None}],
            )
        messages.success(request, f"Usuário \"{user.username}\" desvinculado de {cliente.nome_razao_social}.")
        return redirect("cliente-edit", pk=cliente.pk)
