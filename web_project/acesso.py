"""Regras de escopo e de acesso, centralizadas.

Cadeia de acesso: Usuário -> Cliente -> Segmento -> Módulo habilitado ->
Permissão do perfil -> Dados. Telas, API e menu consultam SEMPRE as funções
deste arquivo — não repita estas regras em views/templates.
"""
from django.contrib.auth.models import Group, User
from django.db.models import Q

GRUPO_ADMINISTRADOR = "Administrador"  # perfil "Admin": acesso global, só no cliente do sistema
PERFIS_PADRAO = ("Gestor", "Operador")  # sempre visíveis na configuração de perfis


def administrador_global(user):
    """True para superusuário ou membro do grupo "Administrador": quem
    administra o sistema inteiro (todos os Clientes). Qualquer outro usuário
    enxerga/gerencia apenas a estrutura (matriz/filiais) do PRÓPRIO Cliente."""
    return bool(user.is_authenticated and (user.is_superuser or user.groups.filter(name=GRUPO_ADMINISTRADOR).exists()))


def cliente_do_usuario(user):
    return getattr(getattr(user, "profile", None), "cliente", None)


# --------------------------------------------------------------------------
# Matriz / Filiais
# --------------------------------------------------------------------------

def clientes_acessiveis(user):
    """Clientes que o usuário pode ver: o próprio, a sua matriz e todas as
    filiais dessa matriz. Admin global: todos. Sem cliente: nenhum."""
    from apps.cliente.models import Cliente

    if administrador_global(user):
        return Cliente.objects.all()
    cliente = cliente_do_usuario(user)
    if cliente is None:
        return Cliente.objects.none()
    return Cliente.objects.filter(Q(pk=cliente.raiz_id) | Q(matriz_id=cliente.raiz_id))


def clientes_dos_itens(user, acao="view"):
    """Clientes cujos ITENS (insumos, categorias...) o usuário pode alcançar
    para a ação ('view', 'add', 'change' ou 'delete'): sempre o próprio
    cliente (a permissão do módulo é checada à parte) e, se for uma MATRIZ
    com a permissão `cliente.<acao>_itensfilial`, também as suas filiais.
    Filial nunca alcança itens da matriz nem de outras filiais."""
    from apps.cliente.models import Cliente

    cliente = cliente_do_usuario(user)
    if cliente is None:
        return Cliente.objects.none()
    filtro = Q(pk=cliente.pk)
    if cliente.tipo_estabelecimento == Cliente.TIPO_MATRIZ and user.has_perm(f"cliente.{acao}_itensfilial"):
        filtro |= Q(matriz_id=cliente.pk)
    queryset = Cliente.objects.filter(filtro)
    if acao == "add":  # não se cadastra em filial desativada
        queryset = queryset.filter(ativo=True)
    return queryset


def usuarios_acessiveis(user):
    """Usuários da mesma estrutura matriz/filiais (todos, para o Admin).
    Superusuários só aparecem para outros superusuários."""
    queryset = User.objects.select_related("profile", "profile__cliente").prefetch_related("groups")
    if not administrador_global(user):
        queryset = queryset.filter(profile__cliente__in=clientes_acessiveis(user))
    if not user.is_superuser:
        queryset = queryset.filter(is_superuser=False)
    return queryset


# --------------------------------------------------------------------------
# Perfis (grupos)
# --------------------------------------------------------------------------

def perfis_visiveis(user):
    """O perfil Admin fica oculto para quem não é Admin."""
    perfis = Group.objects.all()
    if not administrador_global(user):
        perfis = perfis.exclude(name=GRUPO_ADMINISTRADOR)
    return perfis


def validar_vinculo_admin(perfil, cliente):
    """Devolve a mensagem de erro se um usuário ficaria com o perfil Admin
    fora do cliente do sistema (ou None se estiver tudo certo)."""
    if perfil is not None and perfil.name == GRUPO_ADMINISTRADOR and not (cliente and cliente.sistema):
        return "O perfil Admin só pode ser vinculado ao cliente do sistema (SISTEMA FATURANDO CERTO SERVIÇOS LTDA)."
    return None


# --------------------------------------------------------------------------
# Módulos
# --------------------------------------------------------------------------

def modulos_habilitados(user):
    """Slugs dos módulos que o usuário pode usar, ANTES da permissão do
    perfil: ativos no sistema E (essenciais OU, no segmento do cliente E
    habilitados para o cliente). Admin global enxerga todos os ativos.
    O resultado é guardado no objeto `user` (1 consulta por requisição)."""
    if not user.is_authenticated:
        return set()
    if not hasattr(user, "_modulos_habilitados"):
        from apps.modulo.models import Modulo

        ativos = Modulo.objects.filter(ativo=True)
        cliente = cliente_do_usuario(user)
        if administrador_global(user):
            filtro = ativos
        else:
            permitidos = Q(essencial=True)  # Configurações: só dependem da permissão do perfil
            if cliente is not None and cliente.segmento_id:
                permitidos |= Q(
                    segmentos=cliente.segmento_id, segmentos__ativo=True,
                    clientes_habilitados__cliente=cliente, clientes_habilitados__ativo=True,
                )
            filtro = ativos.filter(permitidos)
        user._modulos_habilitados = set(filtro.values_list("slug", flat=True))
    return user._modulos_habilitados
