"""
Mixins reaproveitáveis pelas telas de negócio (Insumo, Categoria, Produto,
Processado, Ficha, Precificação) para aplicar o isolamento multi-tenant
(cada Cliente só enxerga os próprios dados; a matriz também os das filiais,
conforme a permissão) sempre da mesma forma.
"""
from django.contrib import messages
from django.db import models, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View

from apps.auditoria.utils import registrar_exclusao
from web_project.acesso import clientes_dos_itens
from web_project.globais import q_globais_do_usuario


class ClienteScopedMixin:
    """Restringe o queryset de uma ListView/DetailView/UpdateView/DeleteView
    aos Clientes que o usuário pode alcançar para a ação da view
    (`acao_escopo`: 'view', 'add', 'change' ou 'delete').

    Requer que o model tenha um campo `cliente` (FK para cliente.Cliente).

    Escopo (web_project/acesso.py::clientes_dos_itens): o próprio Cliente do
    usuário e, para uma MATRIZ com a permissão `cliente.<acao>_itensfilial`,
    as suas filiais. O escopo vem SEMPRE do usuário autenticado — nunca de
    valor do formulário/POST/querystring —, então um "pk" fora dele resulta
    em 404 em vez de vazar dado ou permitir edição indevida (IDOR).
    """

    acao_escopo = "view"
    # Listagens de canais/formas de pagamento mostram também os itens GLOBAIS liberados ao segmento do usuário (web_project/globais.py).
    # Só na ação "view": editar/excluir continua restrito ao escopo de unidades, então um global de outro dono dá 404.
    incluir_globais = False

    def clientes_do_escopo(self):
        return clientes_dos_itens(self.request.user, self.acao_escopo)

    def get_queryset(self):
        filtro = Q(cliente__in=self.clientes_do_escopo())
        if self.incluir_globais and self.acao_escopo == "view" and not self.request.GET.get("unidade"):
            filtro |= q_globais_do_usuario(self.model, self.request.user)
        return super().get_queryset().select_related("cliente").filter(filtro)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        objeto = getattr(self, "object", None)  # None ao criar
        kwargs["cliente"] = objeto.cliente if objeto is not None else self.request.user.profile.cliente
        kwargs["unidades"] = self.clientes_do_escopo()
        return kwargs

    def form_valid(self, form):
        # Só ao CRIAR define o dono: o escolhido (limitado ao escopo pelo
        # próprio campo do form) ou o cliente do usuário. Editar nunca muda o dono.
        if form.instance.pk is None:
            form.instance.cliente = form.cleaned_data.get("cliente") or self.request.user.profile.cliente
        return super().form_valid(form)


class ListaPorUnidadeMixin(ClienteScopedMixin):
    """Listagem de itens com filtro "Unidade" (matriz/filiais). Só aparece
    para quem alcança mais de uma unidade; o filtro é aplicado SOBRE o escopo."""

    def get_queryset(self):
        queryset = super().get_queryset()
        escolhida = self.request.GET.get("unidade", "")
        return queryset.filter(cliente_id=escolhida) if escolhida.isdigit() else queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        unidades = list(self.clientes_do_escopo().order_by("tipo_estabelecimento", "nome_razao_social"))
        context.update(
            unidades=unidades,
            mostrar_unidade=len(unidades) > 1,
            unidade_selecionada=self.request.GET.get("unidade", ""),
        )
        return context


class SalvarEAdicionarMixin:
    """Botão "Salvar e adicionar outro(a)" nos formulários de criação: depois
    de gravar, volta ao formulário em branco em vez da lista. Basta informar
    `nome_url_novo` (nome da URL de criação) na CreateView e incluir
    templates/partials/botoes_salvar.html no formulário; sem o botão marcado,
    vale o comportamento normal (success_url)."""

    nome_url_novo = None

    def get_success_url(self):
        if self.nome_url_novo and "salvar_e_novo" in self.request.POST:
            return reverse(self.nome_url_novo)
        return super().get_success_url()


def em_uso(objeto):
    """True se algum registro de qualquer tabela referencia `objeto`. Percorre
    as relações reversas do Django (só as que NÃO são CASCADE: referências de
    outros cadastros, não os itens que pertencem ao objeto), então novos models
    que apontarem para ele (ex.: precificação -> ficha) passam a contar sem
    mudar esta regra."""
    for relacao in objeto._meta.related_objects:
        if relacao.on_delete is models.CASCADE:  # filhos que pertencem ao objeto (itens da ficha) não são "uso"
            continue
        if relacao.related_model._default_manager.filter(**{relacao.field.name: objeto}).exists():
            return True
    return False


class ExcluirOuDesativarView(View):
    """POST de exclusão segura, sem CASCADE: item sem nenhum uso é apagado;
    item em uso (produto com fichas, ficha com precificações...) é só
    DESATIVADO, preservando o histórico. Escopo: unidade própria e, para a
    matriz com `cliente.delete_itensfilial`, as filiais. Requer um model com
    `cliente`, `ativo` e `atualizado_em`.

    Configure: `model`, `url_lista` (nome da URL de volta), `rotulo` ("produto"),
    `artigo` ("o"/"a") — usados só nas mensagens."""

    model = None
    url_lista = None
    rotulo = "registro"
    artigo = "o"
    filtro_extra = {}  # restringe o alvo (ex.: só serviços na tela de Serviços)

    def post(self, request, pk):
        with transaction.atomic():
            objeto = get_object_or_404(
                self.model.objects.select_for_update(), pk=pk, cliente__in=clientes_dos_itens(request.user, "delete"), **self.filtro_extra
            )
            nome = str(objeto)
            if em_uso(objeto):
                registrar_exclusao(request, objeto, logica=True)
                objeto.ativo = False
                objeto.save(update_fields=["ativo", "atualizado_em"])
                messages.warning(
                    request, f'{self.artigo.upper()} {self.rotulo} "{nome}" está em uso e não pode ser apagad{self.artigo}; '
                    f"foi apenas desativad{self.artigo}.",
                )
            else:
                registrar_exclusao(request, objeto)  # antes do delete(): depois o objeto perde o id
                objeto.delete()
                messages.success(request, f'{self.rotulo.capitalize()} "{nome}" excluíd{self.artigo} com sucesso.')
        return redirect(self.url_lista)


class ListaPaginadaMixin:
    """Paginação, ordenação e filtro de status padronizados para listagens.

    Tudo vem de listas fechadas: `per_page` só aceita `opcoes_por_pagina` e a
    coluna de `sort` só aceita as de `colunas_ordenaveis` (nunca repassamos a
    querystring direto ao banco). Combine com ListaPorUnidadeMixin ANTES do
    ListView: `class X(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView)`.

    `status_padrao`: "ativo" (sem parâmetro mostra só ativos) ou "todos"."""

    colunas_ordenaveis = {"nome": "nome"}
    ordenacao_padrao = "nome"
    padrao_por_pagina = 25
    opcoes_por_pagina = [10, 25, 50, 100]
    status_padrao = "ativo"
    direcao_padrao = "asc"  # "desc" para listas cronológicas (mais recentes primeiro)

    def get_paginate_by(self, queryset):
        try:
            valor = int(self.request.GET.get("per_page", self.padrao_por_pagina))
        except (TypeError, ValueError):
            valor = self.padrao_por_pagina
        return valor if valor in self.opcoes_por_pagina else self.padrao_por_pagina

    def filtrar_status(self, queryset):
        status = self.request.GET.get("status")
        if status not in ("ativo", "inativo", "todos"):
            status = self.status_padrao
        if status == "todos":
            return queryset
        return queryset.filter(ativo=(status == "ativo"))

    def ordenar(self, queryset):
        campo = self.colunas_ordenaveis.get(self.request.GET.get("sort", self.ordenacao_padrao), self.colunas_ordenaveis[self.ordenacao_padrao])
        return queryset.order_by(f"-{campo}" if self.request.GET.get("dir", self.direcao_padrao) == "desc" else campo, "-pk" if self.direcao_padrao == "desc" else "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        context.update(
            termo_pesquisa=params.get("q", ""), status=params.get("status", ""),
            per_page=self.get_paginate_by(None), opcoes_por_pagina=self.opcoes_por_pagina,
            sort=params.get("sort", self.ordenacao_padrao), dir=params.get("dir", self.direcao_padrao),
        )
        return context
