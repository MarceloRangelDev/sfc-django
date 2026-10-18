"""Regras do Kanban. Acesso: SOMENTE administrador global, validado aqui (serviço) além da rota e da API.

Tenant: o quadro é o do Cliente do usuário autenticado (nunca vem do navegador). Cartão/coluna de outro
Cliente => Http404 (igual a "não existe"). Toda mutação é auditada."""
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, registrar_exclusao, tirar_foto
from web_project.acesso import administrador_global, cliente_do_usuario
from web_project.regras import RegraNegocioError

from .models import Cartao, Coluna

COLUNAS_PADRAO = ("A fazer", "Em andamento", "Concluído")
LIMITE_COLUNAS = 12


def contexto(usuario):
    """Valida o acesso e devolve o Cliente do quadro. Levanta PermissionDenied (não admin) ou RegraNegocioError (sem cliente)."""
    if not administrador_global(usuario):
        raise PermissionDenied("Somente administradores podem acessar o Kanban.")
    cliente = cliente_do_usuario(usuario)
    if cliente is None:
        raise RegraNegocioError("Seu usuário não está vinculado a um cliente.")
    return cliente


def _renumerar(queryset):
    for posicao, objeto in enumerate(queryset.order_by("ordem", "pk")):
        if objeto.ordem != posicao:
            objeto.ordem = posicao
            objeto.save(update_fields=["ordem"])


def quadro(usuario):
    """Colunas (com cartões) do Cliente; cria as colunas padrão no primeiro acesso."""
    cliente = contexto(usuario)
    if not Coluna.objects.filter(cliente=cliente).exists():
        for i, nome in enumerate(COLUNAS_PADRAO):
            Coluna.objects.get_or_create(cliente=cliente, nome=nome, defaults={"ordem": i})
    return list(Coluna.objects.filter(cliente=cliente).prefetch_related("cartoes__responsavel"))


def _coluna(cliente, pk):
    try:
        return Coluna.objects.select_for_update().get(pk=pk, cliente=cliente)
    except (Coluna.DoesNotExist, ValueError, TypeError):
        raise Http404


def _cartao(cliente, pk):
    try:
        return Cartao.objects.select_for_update().get(pk=pk, cliente=cliente)
    except (Cartao.DoesNotExist, ValueError, TypeError):
        raise Http404


def _responsavel(cliente, pk):
    """Responsável precisa pertencer ao mesmo Cliente (não se atribui cartão a usuário de outro tenant)."""
    if not pk:
        return None
    try:
        return User.objects.get(pk=pk, profile__cliente=cliente, is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        raise RegraNegocioError("Responsável inválido.")


def _texto(texto, rotulo, maximo, obrigatorio=True):
    texto = (texto or "").strip()
    if obrigatorio and not texto:
        raise RegraNegocioError(f"Informe {rotulo}.")
    if len(texto) > maximo:
        raise RegraNegocioError(f"{rotulo.capitalize()} deve ter no máximo {maximo} caracteres.")
    return texto


def criar_coluna(request, nome):
    cliente = contexto(request.user)
    nome = _texto(nome, "o nome da coluna", 80)
    with transaction.atomic():
        existentes = Coluna.objects.select_for_update().filter(cliente=cliente)
        total = existentes.count()
        if total >= LIMITE_COLUNAS:
            raise RegraNegocioError(f"O quadro aceita no máximo {LIMITE_COLUNAS} colunas.")
        coluna = Coluna.objects.create(cliente=cliente, nome=nome, ordem=total)
        registrar_criacao(request, coluna, modulo="kanban")
    return coluna


def renomear_coluna(request, pk, nome):
    cliente = contexto(request.user)
    nome = _texto(nome, "o nome da coluna", 80)
    with transaction.atomic():
        coluna = _coluna(cliente, pk)
        antes = tirar_foto(coluna)
        coluna.nome = nome
        coluna.save(update_fields=["nome"])
        alteracoes = comparar(antes, tirar_foto(coluna))
        if alteracoes:
            registrar_alteracoes(request, coluna, alteracoes, modulo="kanban")
    return coluna


def excluir_coluna(request, pk):
    """Só exclui coluna vazia (nunca apaga cartões em silêncio)."""
    cliente = contexto(request.user)
    with transaction.atomic():
        coluna = _coluna(cliente, pk)
        if coluna.cartoes.exists():
            raise RegraNegocioError("Mova ou exclua os cartões da coluna antes de excluí-la.")
        registrar_exclusao(request, coluna, modulo="kanban")
        coluna.delete()
        _renumerar(Coluna.objects.filter(cliente=cliente))


def criar_cartao(request, coluna_id, titulo, descricao="", responsavel_id=None, prazo=None):
    cliente = contexto(request.user)
    titulo = _texto(titulo, "o título", 150)
    descricao = _texto(descricao, "a descrição", 2000, obrigatorio=False)
    with transaction.atomic():
        coluna = _coluna(cliente, coluna_id)
        cartao = Cartao.objects.create(
            cliente=cliente, coluna=coluna, titulo=titulo, descricao=descricao, responsavel=_responsavel(cliente, responsavel_id),
            prazo=prazo, ordem=coluna.cartoes.count(), criado_por=request.user,
        )
        registrar_criacao(request, cartao, modulo="kanban")
    return cartao


def editar_cartao(request, pk, titulo, descricao="", responsavel_id=None, prazo=None):
    cliente = contexto(request.user)
    titulo = _texto(titulo, "o título", 150)
    descricao = _texto(descricao, "a descrição", 2000, obrigatorio=False)
    with transaction.atomic():
        cartao = _cartao(cliente, pk)
        antes = tirar_foto(cartao)
        cartao.titulo, cartao.descricao, cartao.prazo = titulo, descricao, prazo
        cartao.responsavel = _responsavel(cliente, responsavel_id)
        cartao.save()
        alteracoes = comparar(antes, tirar_foto(cartao))
        if alteracoes:
            registrar_alteracoes(request, cartao, alteracoes, modulo="kanban")
    return cartao


def excluir_cartao(request, pk):
    cliente = contexto(request.user)
    with transaction.atomic():
        cartao = _cartao(cliente, pk)
        registrar_exclusao(request, cartao, modulo="kanban")
        coluna = cartao.coluna
        cartao.delete()
        _renumerar(coluna.cartoes)


def mover_cartao(request, pk, coluna_id, posicao):
    """Move o cartão para `coluna_id` na posição `posicao` (0 = topo; valores fora da faixa são ajustados).
    Renumera origem e destino na mesma transação e audita a mudança de coluna."""
    cliente = contexto(request.user)
    if not request.user.has_perm("kanban.mover_cartao"):
        raise PermissionDenied("Sem permissão para mover cartões.")
    try:
        posicao = int(posicao)
    except (TypeError, ValueError):
        raise RegraNegocioError("Posição inválida.")
    with transaction.atomic():
        cartao = _cartao(cliente, pk)
        destino = _coluna(cliente, coluna_id)
        origem = cartao.coluna
        ids = list(Cartao.objects.filter(coluna=destino).exclude(pk=cartao.pk).order_by("ordem", "pk").values_list("pk", flat=True))
        ids.insert(max(0, min(posicao, len(ids))), cartao.pk)
        cartao.coluna = destino
        cartao.save(update_fields=["coluna", "atualizado_em"])
        for ordem, i in enumerate(ids):
            Cartao.objects.filter(pk=i, cliente=cliente).update(ordem=ordem)
        if origem.pk != destino.pk:
            _renumerar(origem.cartoes)
            registrar_alteracoes(
                request, cartao, [{"campo": "coluna", "rotulo": "Coluna", "antes": origem.nome, "depois": destino.nome}], modulo="kanban",
            )
    cartao.refresh_from_db()
    return cartao


def serializar(coluna):
    """Dados do quadro para tela/API (sem campos internos)."""
    return {
        "id": coluna.pk, "nome": coluna.nome,
        "cartoes": [
            {"id": c.pk, "titulo": c.titulo, "descricao": c.descricao, "prazo": c.prazo.isoformat() if c.prazo else None,
             "responsavel_id": c.responsavel_id, "responsavel": (c.responsavel.get_full_name() or c.responsavel.username) if c.responsavel else None}
            for c in coluna.cartoes.all()
        ],
    }
